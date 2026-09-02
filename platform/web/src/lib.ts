// 설정 로드 + Cognito 인증 + 단일 WebSocket 관리자

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
let _idToken: string | null = null;
let _email: string | null = null;
export const auth = {
  get token() { return _accessToken; },
  get idToken() { return _idToken; },
  get email() { return _email; },
  logout() { _accessToken = null; _idToken = null; _email = null; sock.close(); },
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
  _idToken = data.AuthenticationResult.IdToken;
  _email = email;
}

export type WsEvent = { type: string; [k: string]: any };
type Handler = (e: WsEvent) => void;

/** 단일 WebSocket — 요청/응답(reqId 매칭) + 스트림 이벤트 구독. */
class PlatformSocket {
  private ws: WebSocket | null = null;
  private pending = new Map<string, (e: WsEvent) => void>();
  private subs = new Set<Handler>();
  private seq = 0;

  async ensure(): Promise<WebSocket> {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return this.ws;
    const cfg = await loadConfig();
    if (!_accessToken) throw new Error('로그인이 필요합니다.');
    return new Promise((resolve, reject) => {
      const ws = new WebSocket(`${cfg.wssUrl}?token=${encodeURIComponent(_accessToken!)}`);
      ws.onopen = () => { this.ws = ws; resolve(ws); };
      ws.onerror = () => reject(new Error('서버 연결에 실패했습니다.'));
      ws.onmessage = (m) => {
        let e: WsEvent; try { e = JSON.parse(m.data); } catch { return; }
        if (e.reqId && this.pending.has(e.reqId)) {
          this.pending.get(e.reqId)!(e);
          if (!e.type.endsWith('.stage') && !e.type.endsWith('.token')) this.pending.delete(e.reqId);
        }
        this.subs.forEach(h => h(e));
      };
      ws.onclose = () => { this.ws = null; };
    });
  }

  /** 단발 요청 — 응답 이벤트 1건을 기다린다. */
  async request(action: string, payload: Record<string, any> = {}): Promise<WsEvent> {
    const ws = await this.ensure();
    const reqId = `r${++this.seq}`;
    return new Promise((resolve, reject) => {
      const to = setTimeout(() => { this.pending.delete(reqId); reject(new Error('시간 초과')); }, 120000);
      this.pending.set(reqId, (e) => {
        if (e.type.endsWith('.stage') || e.type.endsWith('.token')) return; // 스트림은 구독으로
        clearTimeout(to); resolve(e);
      });
      ws.send(JSON.stringify({ action, reqId, ...payload }));
    });
  }

  /** 스트리밍 실행 — 완료 이벤트가 올 때까지 모든 이벤트를 핸들러로 보낸다. */
  async run(action: string, payload: Record<string, any>, onEvent: Handler): Promise<void> {
    const ws = await this.ensure();
    const reqId = `r${++this.seq}`;
    return new Promise((resolve, reject) => {
      const h: Handler = (e) => {
        onEvent(e);
        if (e.type === 'error') { this.subs.delete(h); reject(new Error(e.message)); }
      };
      this.subs.add(h);
      // s1은 vector.done+graph.done 2건, s2는 s2.done 1건에서 종료
      let doneNeeded = action === 's1' ? 2 : 1;
      const done: Handler = (e) => {
        if (e.type.endsWith('.done') && --doneNeeded <= 0) {
          this.subs.delete(h); this.subs.delete(done); resolve();
        }
      };
      this.subs.add(done);
      ws.send(JSON.stringify({ action, reqId, ...payload }));
    });
  }

  close() { this.ws?.close(); this.ws = null; }
}

export const sock = new PlatformSocket();
