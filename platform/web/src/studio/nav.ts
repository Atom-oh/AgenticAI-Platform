// platform/web/src/studio/nav.ts
// 온톨로지 탐색기로 이동 — S1.tsx의 GraphView onOpen과 동일한 패턴(App.tsx의 #/explore 해시 라우트,
// Views.tsx의 setPendingExploreNode로 대상 노드 전달).
export function openExplorer(nodeId: string) {
  import('../Views').then(v => { v.setPendingExploreNode(nodeId); location.hash = '#/explore'; });
}
