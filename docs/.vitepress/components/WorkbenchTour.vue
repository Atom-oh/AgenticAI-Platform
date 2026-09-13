<script setup lang="ts">
import { computed, ref } from 'vue'
import { withBase } from 'vitepress'
import chapters from '../../../videos/ontology-workbench/chapters.json'

const player = ref<HTMLVideoElement>()
const selected = ref('planner')
const roles = [
  { id: 'planner', name: '기획자', title: '상품 조건과 업무 흐름을 기준으로',
    menu: '상품기획서 → 변경 요청·영향 분석 → 업무 흐름·가이드',
    tasks: ['상품 조건과 안내 문구의 기준안·제안안을 기록합니다.', '연결된 화면과 가이드의 영향 경로를 검토합니다.', '담당 작업과 검토할 근거를 팀에 전달합니다.'], chapter: 2 },
  { id: 'designer', name: '디자이너', title: '기준이 있는 디자인 스튜디오',
    menu: '디자인 스튜디오 → 디자인 산출물 → 디자인 시스템·자산',
    tasks: ['상품 지침과 반입 자료로 화면 작업을 시작합니다.', '실제 생성 화면과 기능·화면 검증 기록을 확인합니다.', '기준이 바뀌면 영향을 받는 산출물을 다시 검토합니다.'], chapter: 3 },
  { id: 'developer', name: '개발자', title: '변경 대상에서 검증된 코드까지',
    menu: 'React 컴포넌트 → 개발 작업·검증 → 스튜디오 릴리스',
    tasks: ['사용 가능한 실제 React 타입과 버전을 확인합니다.', '변경 근거와 담당 작업을 연결해 수정합니다.', '승인한 결과물의 재빌드·검증·개발 전달 기록을 확인합니다.'], chapter: 3 },
  { id: 'business', name: '업무 담당자', title: '계산과 근거를 확인하는 업무',
    menu: '연금 상담 → 직원 평가 → 보고서 작업실',
    tasks: ['합성 페르소나의 계산 가정과 연금 인사이트를 확인합니다.', '기준 응답과 모델 응답을 구분하고 실제 답변을 평가합니다.', '내부 검토 보고서의 출처·버전·미확인 항목을 확인합니다.'], chapter: 6 },
  { id: 'operator', name: '운영자', title: '지식 원본과 도구의 제공 범위 관리',
    menu: '지식 원본 등록 → 수집·ETL 배치 → MCP 도구 등록',
    tasks: ['승인된 연결과 프로젝트별 수집 범위를 관리합니다.', '원본 권한과 벡터·그래프 게시 상태를 확인합니다.', '내부 검색·근거 조회 도구의 제공 범위를 등록합니다.'], chapter: 4 },
]
const role = computed(() => roles.find(item => item.id === selected.value)!)
async function seek(index: number) {
  if (!player.value) return
  player.value.currentTime = chapters[index].start
  player.value.scrollIntoView({ behavior: 'smooth', block: 'center' })
  try { await player.value.play() } catch { player.value.focus() }
}
</script>

<template>
  <div class="tour">
    <div class="tour-intro">
      <span class="tour-eyebrow">일하는 사람을 중심으로 연결한 플랫폼</span>
      <h2>변경을 찾고,<br>다음 작업까지 연결합니다.</h2>
      <p>상품·규정·화면·컴포넌트의 관계를 따라 영향 범위를 확인하고,<br class="wide-only"> 각 담당자의 작업과 검증 근거를 같은 프로젝트에 남깁니다.</p>
      <div class="tour-tags"><span>직무별 작업실</span><span>지식 · 온톨로지</span><span>Skill 제작</span><span>연금 · 내부 보고서</span></div>
    </div>
    <video ref="player" controls playsinline preload="metadata" tabindex="0"
      :poster="withBase('/media/ontology-workbench-poster.jpg')"
      aria-label="업무별 작업실 구현 설명 영상, 4분">
      <source :src="withBase('/media/ontology-workbench.mp4')" type='video/mp4; codecs="avc1.640032, mp4a.40.2"'>
      <source :src="withBase('/media/ontology-workbench.webm')" type='video/webm; codecs="vp9, opus"'>
      <track kind="captions" srclang="ko" label="한국어 자막" :src="withBase('/media/ontology-workbench.vtt')">
      브라우저에서 영상을 재생하지 못하면 아래 다운로드를 이용하세요.
    </video>
    <div class="tour-video-meta"><span>4분 · 한국어 음성 · 화면 자막 · 합성 데이터</span>
      <a :href="withBase('/media/ontology-workbench.mp4')" download>영상 다운로드 ↓</a></div>
    <div class="tour-chapters" aria-label="영상 챕터">
      <button v-for="(chapter, index) in chapters" :key="chapter.id" type="button" @click="seek(index)">
        <span>{{ String(Math.floor(chapter.start / 60)).padStart(2, '0') }}:{{ String(Math.floor(chapter.start % 60)).padStart(2, '0') }}</span>{{ chapter.title }}
      </button>
    </div>
    <section class="tour-roles">
      <h2>내 업무에서는 이렇게 씁니다</h2>
      <div class="tour-role-tabs" aria-label="담당 업무 선택">
        <button v-for="item in roles" :key="item.id" type="button" :aria-pressed="selected === item.id" @click="selected = item.id">{{ item.name }}</button>
      </div>
      <div class="tour-role-body">
        <span class="tour-eyebrow">{{ role.name }}의 시작점</span>
        <h3>{{ role.title }}</h3><p class="tour-menu">{{ role.menu }}</p>
        <ol><li v-for="task in role.tasks" :key="task">{{ task }}</li></ol>
        <button class="tour-watch" type="button" @click="seek(role.chapter)">관련 영상으로 이동 →</button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.tour{--ink:#143e3e;--accent:#007a78;margin:24px 0 38px;color:var(--vp-c-text-1)}
.tour-intro{padding:38px 36px;background:#eff7f4;border:1px solid #cae2db;border-radius:24px;color:var(--ink)}
.tour-eyebrow{font-size:13px;font-weight:700;color:var(--accent);letter-spacing:.03em}
.tour-intro h2{font-size:36px;line-height:1.35;letter-spacing:-1.2px;border:0;margin:14px 0;padding:0}
.tour-intro p{font-size:16px;line-height:1.9;margin:16px 0}
.tour-tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:24px}
.tour-tags span{background:#fff;padding:5px 11px;border:1px solid #cae2db;border-radius:100px;font-size:12px}
video{display:block;width:100%;aspect-ratio:16/9;margin-top:28px;border-radius:16px;background:#123d3d;box-shadow:0 18px 45px #123d3d16}
.tour-video-meta{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;font-size:12px;margin:12px 2px;color:var(--vp-c-text-2)}
.tour-video-meta a{font-weight:600}.tour-chapters{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:20px 0 36px}
.tour-chapters button{display:flex;gap:12px;padding:12px;text-align:left;border:1px solid var(--vp-c-divider);border-radius:9px;font-size:12px;line-height:1.5}
.tour-chapters button:hover,.tour-chapters button:focus-visible{border-color:var(--accent);color:var(--accent)}
.tour-chapters span{font-variant-numeric:tabular-nums;color:var(--accent);flex-shrink:0;font-weight:700}
.tour-role-tabs{display:flex;flex-wrap:wrap;gap:8px;margin:20px 0}
.tour-role-tabs button{border-radius:10px;padding:9px 16px;border:1px solid var(--vp-c-divider);font-size:14px}
.tour-role-tabs button[aria-pressed=true]{color:white;background:var(--accent);border-color:var(--accent)}
.tour-role-body{border:1px solid var(--vp-c-divider);border-radius:16px;padding:26px}
.tour-role-body h3{margin:8px 0 14px;font-size:24px}.tour-menu{font-size:13px;color:var(--vp-c-text-2)}
.tour-role-body ol{margin:20px 0;padding-left:22px;font-size:15px}.tour-role-body li{margin:12px 0}
.tour-watch{font-size:13px;color:var(--accent);font-weight:700;margin-top:10px}
@media(max-width:600px){.tour-intro{padding:26px 22px}.tour-intro h2{font-size:28px}.tour-chapters{grid-template-columns:1fr}.wide-only{display:none}.tour-role-body{padding:22px}}
</style>
