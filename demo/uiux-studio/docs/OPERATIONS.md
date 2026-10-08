# 배포 기록과 이전 배포 정리

이 문서는 별도 `demo/uiux-studio` 운영 절차입니다. 소스의 이름을 바꿔도 AWS의
기존 stack, Runtime, Gateway, Memory, 버킷은 이전되지 않습니다. 새 이름의
`BankUiuxPlatform` 배포와 이전 배포는 서로 다른 리소스로 관리합니다.

`config/stack.json`은 미설정 표시이며 실행 가능한 배포 출력이 아닙니다.
운영 스크립트는 `BANK_UIUX_CONFIG`가 가리키는 비공개 파일을 사용합니다.
기본 경로는 저장소의 `.local/uiux-studio/deployment.json`입니다. 실제 ID,
ARN, 도메인의 접두사를 문자열 치환해서는 안 됩니다.

## 실제 배포 기록 만들기

관리자가 대상 계정·리전·stack과 사용할 프로파일을 확인한 뒤 실행합니다.
변수에는 실제 운영 대상 값을 지정합니다. 아래 명령은 stack과 명시한 서비스의
조회 결과만 저장하며, 새 AWS 리소스를 만들지 않습니다.

```bash
python scripts/write_config.py \
  --stack "$UIUX_STACK" --account "$UIUX_ACCOUNT_ID" --region "$UIUX_REGION" \
  --profile "$UIUX_PROFILE" --output "$UIUX_CONFIG"
```

기존 별도 서비스를 포함하려면 실제 조회한 ID를 `--runtime-id`, `--gateway-id`,
`--memory-id`로 전달합니다. Runtime과 Gateway는 stack의 역할 ARN과 일치해야
합니다. Runtime에서 관측한 ECR 저장소와 M2M secret ARN도 기록합니다. secret
값은 기록하지 않습니다. 같은 stack 인스턴스의 기존 비공개 기록을 갱신할 때는
저장된 서비스 ID를 다시 조회합니다. 같은 이름으로 재생성된 다른 stack에는
기존 서비스 ID를 자동으로 붙이지 않습니다.

파일의 `status: observed`는 조회 기록이라는 뜻입니다. 서비스 가동이나 현재
모델 호출 성공을 인증하지 않습니다. 이름이 다른 이전 stack도 기록할 수 있지만,
새 namespace의 배포 스크립트가 그 stack을 암묵적으로 이전하지는 않습니다.

모든 운영 SDK 클라이언트는 기록된 계정과 STS 호출자 계정의 일치를 확인합니다.
`samples` 계정은 `samples-atomoh` 프로파일과 `atomoh` AssumeRole 세션이 필요합니다.
프로파일은 명령의 `BANK_UIUX_PROFILE` 또는 기록된 프로파일로 선택하며 시스템의
기본 AWS 설정 파일을 변경하지 않습니다.

## 새 namespace 배포와 모델 권한

1. `infra/`의 새 stack을 검토하고 배포한 후 실제 출력을 비공개 기록으로 만듭니다.
2. 선택한 모델을 `write_config.py --model-profile "$UIUX_MODEL_PROFILE"`로 조회합니다.
   여러 모델은 옵션을 반복합니다. 요청한 profile을 찾지 못하면 중단하며 다른 모델로
   대체하지 않습니다. 기록의 `model_resources`는 profile과 대상 모델의 실제 ARN입니다.
3. 이 ARN 목록을 CDK의 `modelResources` context에 JSON 배열로 지정해 정책을 검토·반영합니다.
   `modelResources`가 없으면 Runtime 역할에 모델 호출 권한을 주지 않습니다.
4. `write_config.py`로 실제 stack 출력을 다시 읽습니다. Runtime 배포 스크립트는
   선택한 모델 ARN이 실제 stack의 `configured_model_resources`에 포함되어야 진행합니다.
5. 비공개 기록을 사용하는 Gateway·Memory·Runtime 구성 단계를 실행합니다.
   예: `BANK_UIUX_CONFIG="$UIUX_CONFIG" python scripts/deploy_gateway.py`.
   생성된 실제 ID를 기록하며, 동일 이름의 서비스라도 stack 역할이 다르면 갱신하지 않습니다.
6. 관측된 Runtime·Memory ARN을 CDK `runtimeArn`, `memoryArn` context에 지정하면
   초기 애플리케이션 이름 범위의 권한을 그 리소스로 더 좁힐 수 있습니다.
7. Gallery·skills를 게시하고 합성 입력으로 호출·권한·실패 경로를 검증합니다.
   원본 고객 자료는 이 공개 HTML 데모에 넣지 않습니다. 본 플랫폼의 비공개
   React 작업공간이나 AgentCore 통합 완료로 이 결과를 대체하지 않습니다.

동일 source 이름의 재배포도 환경 설정을 변경할 수 있습니다. script로 연결한
Runtime·Memory 설정과 실제 정책을 확인한 후 사용자 요청을 받습니다. 이전
배포의 도메인, 데이터, 권한이 새 namespace로 자동 이전됐다고 표시하지 않습니다.

## 이전 배포 정리

기존 출력과 실제 서비스 ID를 별도 비공개 파일로 기록합니다. 데이터 보존 의무,
승인된 시안·버킷 자료의 백업, 다른 시스템이 같은 ECR 저장소나 secret을 사용하는지
확인합니다. 아래 미리보기는 기록된 리소스만 표시하며 이름 접두사로 전체 계정을
검색해 삭제 대상을 고르지 않습니다.

```bash
python scripts/teardown.py --config "$LEGACY_UIUX_CONFIG"
```

대상과 보존 자료를 검토한 후 명시적으로 실행합니다.

```bash
python scripts/teardown.py --config "$LEGACY_UIUX_CONFIG" --execute
```

Runtime·Gateway의 역할을 다시 확인하고, 비동기 삭제가 끝나기 전에는 다음
의존 리소스를 삭제하지 않습니다. 기록된 Memory, ECR 저장소와 M2M secret도
대상에 포함됩니다. ECR 이미지는 삭제되므로 재사용·보존 여부를 먼저 확인해야
합니다. secret은 7일 복구 기간으로 삭제 예약합니다.

CloudFormation stack까지 정리하려면 `--include-stack`을 추가합니다. 코드에
새로 적힌 stack 이름이 아니라 비공개 기록의 정확한 `stack_id`를 사용합니다.
CloudFormation 요청 후 실제 삭제 완료와 잔여 리소스를 별도로 확인합니다.
Figma 자격 증명의 폐기와 외부 연동 해제도 완료해야 합니다. 이전 stack을
정리하려고 새 이름의 `cdk destroy`를 대신 실행해서는 안 됩니다.
