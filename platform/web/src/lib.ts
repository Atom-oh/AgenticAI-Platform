// 설정 로드 + Cognito 인증 + WebSocket 유틸

export type AppConfig = { wssUrl: string; cognitoClientId: string; region: string };

let _cfg: AppConfig | null = null;
export async function loadConfig(): Promise<AppConfig> {
  if (_cfg) return _cfg;
  const r = await fetch('/config.json');
  _cfg = (await r.json()) as AppConfig;
  return _cfg;
}

// 토큰은 메모리에만 둔다 — 브라우저 스토리지에 저장하지 않는다 (SPEC §12.10)
let _accessToken: string | null = null;
let _email: string | null = null;
export const auth = {
  get token() { return _accessToken; },
  get email() { return _email; },
  logout() { _accessToken = null; _email = null; },
};

export async function login(email: string, password: string): Promise<void> {
  const cfg = await loadConfig();
  const r = await fetch(`https://cognito-idp.${cfg.region}.amazonaws.com/`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-amz-json-1.1',
      'X-Amz-Target': 'AWSCognitoIdentityProviderService.InitiateAuth',
    },
    body: JSON.stringify({
      AuthFlow: 'USER_PASSWORD_AUTH',
      ClientId: cfg.cognitoClientId,
      AuthParameters: { USERNAME: email, PASSWORD: password },
    }),
  });
  const data = await r.json();
  if (!r.ok || !data.AuthenticationResult) {
    throw new Error(data.message || '로그인에 실패했습니다.');
  }
  _accessToken = data.AuthenticationResult.AccessToken;
  _email = email;
}

export type WsEvent = { type: string; [k: string]: any };

export function openS1Socket(onEvent: (e: WsEvent) => void,
                             onClose: () => void): Promise<WebSocket> {
  return new Promise(async (resolve, reject) => {
    const cfg = await loadConfig();
    if (!_accessToken) { reject(new Error('로그인이 필요합니다.')); return; }
    const ws = new WebSocket(`${cfg.wssUrl}?token=${encodeURIComponent(_accessToken)}`);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error('연결에 실패했습니다.'));
    ws.onmessage = (m) => { try { onEvent(JSON.parse(m.data)); } catch { /* skip */ } };
    ws.onclose = onClose;
  });
}
