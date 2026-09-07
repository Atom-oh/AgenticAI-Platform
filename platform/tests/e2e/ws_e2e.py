#!/usr/bin/env python3
"""백엔드 e2e — Cognito 로그인 → WebSocket → 시나리오 액션 실행. 결과 JSON 요약 출력.
사용: DEMO_USER_PASSWORD=... python3 e2e_ws.py [scenarios...]   (기본: 전부)
outputs.json(BankPlatformCore)에서 WssUrl·CognitoClientId를 읽는다."""
import asyncio, json, os, sys, time
import boto3, websockets

OUT = json.load(open('/home/atomoh/AgenticAI-Platform/platform/infra/outputs.json'))['BankPlatformCore']
WSS, CLIENT = OUT['WssUrl'], OUT['CognitoClientId']
EMAIL = os.environ.get('DEMO_USER_EMAIL', 'demo@atomai.click')
PW = os.environ['DEMO_USER_PASSWORD']
S1Q = '전세자금대출 담보 인정 규정이 개정되면 영향받는 상품 · 화면 · 컴포넌트 · 담당부서 · 수정이 필요한 문서는?'
S2Q = '제가 이 상품 우대금리 조건 충족하나요? 얼마나 받을 수 있죠?'
S5Q = '어떤 상품이 제일 돈 많이 벌어요?'
S3Q = '여신 심사 결과 조회 화면을 만들어줘'

def login():
    r = boto3.client('cognito-idp', region_name='ap-northeast-2').initiate_auth(
        AuthFlow='USER_PASSWORD_AUTH', ClientId=CLIENT, AuthParameters={'USERNAME': EMAIL, 'PASSWORD': PW})
    return r['AuthenticationResult']['AccessToken'], r['AuthenticationResult']['IdToken']

class Client:
    def __init__(self, ws): self.ws = ws; self.seq = 0
    async def request(self, action, payload=None, timeout=120):
        self.seq += 1; rid = f'e{self.seq}'
        await self.ws.send(json.dumps({'action': action, 'reqId': rid, **(payload or {})}))
        t0 = time.time()
        while time.time() - t0 < timeout:
            ev = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if 'type' not in ev:
                continue
            if ev.get('reqId') == rid and not ev['type'].endswith('.stage') and not ev['type'].endswith('.token'):
                return ev
        raise TimeoutError(action)
    async def run(self, action, payload, done_needed=1, timeout=150):
        self.seq += 1; rid = f'e{self.seq}'
        await self.ws.send(json.dumps({'action': action, 'reqId': rid, **(payload or {})}))
        t0 = time.time(); first_token = None; events = []; tokens = 0; done = 0
        while time.time() - t0 < timeout:
            ev = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if 'type' not in ev:  # API Gateway 29초 통합 타임아웃 프레임 — Lambda 는 계속 push 하므로 기록만 하고 계속 받는다
                events.append({'type': 'gateway_frame', 'message': json.dumps(ev, ensure_ascii=False)[:160]}); continue
            if ev.get('reqId') not in (rid, None): continue
            if ev['type'] == 'cache.replay': done_needed = 2 if action == 's1' else 1
            if ev['type'].endswith('.token'):
                tokens += 1
                if first_token is None: first_token = round(time.time() - t0, 2)
                continue
            events.append(ev)
            if ev['type'] == 'error': break
            if ev['type'].endswith('.done'):
                done += 1
                if done >= done_needed: break
        return {'elapsed': round(time.time() - t0, 1), 'firstToken': first_token, 'tokens': tokens, 'events': events}

def brief(ev, keys): return {k: ev.get(k) for k in keys if k in ev}

async def main(which):
    access, idtok = login()
    res = {}
    async with websockets.connect(f'{WSS}?token={access}', max_size=8_000_000, open_timeout=30) as ws:
        c = Client(ws)
        if 'hub' in which:
            h = await c.request('hub', {'idToken': idtok}); res['hub'] = brief(h, ['registry','registryApproved','graphNodes','graphEdges','backend','plane','planeLabel','surfaces','llmRoute','genModel'])
        if 'registry' in which:
            r = await c.request('registry_list', {'type': 'ALL'}); res['registry_list'] = {'records': len(r.get('records', [])), 'counts': r.get('counts')}
            cons = await c.request('registry_consumer', {'subtype': 'COMPONENT'}); res['consumer'] = [f"{x['name']}@{x.get('recordVersion')}" for x in cons.get('records', cons.get('components', []))][:12]
        if 's1' in which:
            r = await c.run('s1', {'query': S1Q}, done_needed=2)
            meta = next((e for e in r['events'] if e['type'] == 'graph.meta'), {}); gd = next((e for e in r['events'] if e['type'] == 'graph.done'), {}); vd = next((e for e in r['events'] if e['type'] == 'vector.done'), {}); vc = next((e for e in r['events'] if e['type'] == 'vector.chunks'), {})
            res['s1'] = {'elapsed': r['elapsed'], 'firstToken': r['firstToken'], 'tokens': r['tokens'], 'seed': meta.get('seed'), 'counts': meta.get('counts'), 'graphNodes': len((meta.get('graph') or {}).get('nodes', [])), 'hallucinated': gd.get('hallucinatedIds'), 'modelId': gd.get('modelId'), 'searchPlane': vc.get('searchPlane'), 'vectorError': vd.get('error'), 'errors': [e.get('message') for e in r['events'] if e['type']=='error']}
        if 's2' in which:
            r = await c.run('s2', {'query': S2Q}); stages = [e['step'] for e in r['events'] if e['type']=='s2.stage']; d = next((e for e in r['events'] if e['type']=='s2.done'), {})
            mask = next((e for e in r['events'] if e['type']=='s2.stage' and e.get('step')=='mask'), {})
            res['s2'] = {'elapsed': r['elapsed'], 'firstToken': r['firstToken'], 'tokens': r['tokens'], 'stages': stages, 'blocked': d.get('blocked'), 'invented': d.get('inventedNumbers'), 'guardrailOut': (d.get('guardrailOut') or {}).get('action'), 'plane': d.get('plane'), 'planeLabel': d.get('planeLabel'), 'modelId': d.get('modelId'), 'piiOutbound': mask.get('piiOutbound'), 'maskedFields': [f.get('field') for f in mask.get('maskedFields', [])], 'errors': [e.get('message') for e in r['events'] if e['type']=='error'], 'doneError': d.get('error')}
        if 's5' in which:
            r = await c.run('s2', {'query': S5Q}); d = next((e for e in r['events'] if e['type']=='s2.done'), {}); res['s5'] = {'elapsed': r['elapsed'], 'blocked': d.get('blocked'), 'topics': d.get('topics'), 'message': (d.get('message') or '')[:80]}
        if 'screengen' in which:
            r = await c.run('screengen', {'prompt': S3Q}); d = next((e for e in r['events'] if e['type']=='screengen.done'), {})
            res['screengen'] = {'elapsed': r['elapsed'], 'tokens': r['tokens'], 'ok': d.get('ok'), 'attempts': d.get('attempts'), 'components': d.get('componentsUsed'), 'gates': {k: (v.get('ok') if isinstance(v, dict) else v) for k, v in (d.get('gates') or {}).items()}, 'codeChars': len(d.get('code') or ''), 'error': d.get('error'), 'errors': [e.get('message') for e in r['events'] if e['type']=='error']}
        if 'report' in which:
            r = await c.run('report', {}); d = next((e for e in r['events'] if e['type']=='report.done'), {}); stg = [e.get('step') for e in r['events'] if e['type']=='report.stage']
            rs = next((e for e in r['events'] if e['type']=='report.stage' and e.get('step')=='reader_summarize'), {})
            res['report'] = {'elapsed': r['elapsed'], 'stages': stg, 'denied': len(rs.get('deniedAttempts', []) or []), 'injection': (rs.get('summary') or {}).get('injectionDetected'), 'reportChars': len(d.get('report') or ''), 'error': d.get('error'), 'errors': [e.get('message') for e in r['events'] if e['type']=='error']}
        if 'agents' in which:
            cat = await c.request('agents_catalog', {}); res['agents_catalog'] = [{'name': a['name'], 'status': a.get('status'), 'harness': a.get('harnessStatus'), 'runtime': (a.get('runtime') or '')} for a in cat.get('agents', [])][:8]
            r = await c.run('agent_invoke', {'name': 'regulation_impact_agent', 'message': S1Q}, timeout=180); d = next((e for e in r['events'] if e['type']=='agent.done'), {}); tools = [e.get('name') for e in r['events'] if e['type']=='agent.stage' and e.get('step')=='tool_start']
            res['agent_invoke'] = {'elapsed': r['elapsed'], 'firstToken': r['firstToken'], 'tokens': r['tokens'], 'tools': tools, 'error': d.get('error'), 'usage': d.get('usage'), 'modelId': d.get('modelId'), 'runtime': d.get('runtime')}
        if 'portal' in which:
            p = await c.request('portal_list', {'category': 'Components'}); cards = p.get('cards') or p.get('items') or []; res['portal'] = {'cards': len(cards), 'sample': cards[:1]}
            imp = await c.request('portal_impact', {'id': 'CMP-Button-v2'}); res['portal_impact'] = imp.get('counts')
        if 'design' in which:
            cat = await c.request('design_catalog', {}); res['design_catalog'] = {'source': cat.get('source'), 'specs': [x['id'] for x in cat.get('productSpecs', [])], 'checklists': [x['id'] for x in cat.get('checklists', [])], 'runtime': cat.get('runtime')}
            pv = await c.request('design_preview', {'productSpecId': 'ps-soccer-club-savings'}); res['design_preview'] = {'steps': [x['id'] for x in (pv.get('prd') or {}).get('steps', [])], 'branchSteps': (pv.get('prd') or {}).get('branchSteps'), 'counts': pv.get('counts'), 'error': pv.get('error')}
            r = await c.run('design_flow', {'productSpecId': 'ps-soccer-club-savings'}, timeout=300); d = next((e for e in r['events'] if e['type']=='design.done'), {}); steps = [e.get('step') for e in r['events'] if e['type']=='design.stage']
            rep = d.get('report') or {}
            res['design_flow'] = {'elapsed': r['elapsed'], 'firstToken': r['firstToken'], 'stages': steps, 'ok': d.get('ok'), 'attempts': d.get('attempts'), 'regenerated': d.get('regenerated'), 'score': rep.get('score'), 'openItems': rep.get('openItems'), 'flowSteps': [x['id'] for x in d.get('steps', [])], 'evidenceStep': any(x['id']=='evidence-soccer-club' for x in d.get('steps', [])), 'runtime': d.get('runtime'), 'usage': d.get('usage'), 'error': d.get('error'), 'errors': [e.get('message') for e in r['events'] if e['type']=='error']}
        if 'traces' in which:
            t = await c.request('traces', {}); res['traces'] = brief(t, ['requests','piiOutboundTotal','tokensOutTotal','blocked','cached','plane','models','retained'])
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


def _dump(res):
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str), flush=True)

if __name__ == '__main__':
    which = sys.argv[1:] or ['hub','registry','s1','s2','s5','screengen','report','agents','portal','design','traces']
    asyncio.run(main(which))
