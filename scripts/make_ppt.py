"""
TBIT 클라우드 MSP 해커톤 발표 PPT 생성 스크립트
실행: py scripts/make_ppt.py
출력: AI_Incident_Response_발표.pptx
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt
import copy

# ── 색상 팔레트 ────────────────────────────────────────────────────────────────
NAVY     = RGBColor(0x0D, 0x1B, 0x3E)   # 배경 (짙은 남색)
BLUE     = RGBColor(0x1A, 0x73, 0xE8)   # 포인트 (밝은 파랑)
CYAN     = RGBColor(0x00, 0xC8, 0xFF)   # 강조
WHITE    = RGBColor(0xFF, 0xFF, 0xFF)
GRAY     = RGBColor(0xB0, 0xBE, 0xC5)
ORANGE   = RGBColor(0xFF, 0x6D, 0x00)   # 경고/위험 강조
GREEN    = RGBColor(0x00, 0xC8, 0x53)   # 효과/긍정
LIGHT_BG = RGBColor(0xF0, 0xF4, 0xFF)   # 밝은 배경 슬라이드

W = Inches(13.33)   # 와이드스크린 16:9
H = Inches(7.5)

prs = Presentation()
prs.slide_width  = W
prs.slide_height = H

blank = prs.slide_layouts[6]   # 완전 빈 레이아웃


# ── 유틸 함수 ──────────────────────────────────────────────────────────────────

def add_rect(slide, x, y, w, h, fill=None, line=None, alpha=None):
    shape = slide.shapes.add_shape(1, x, y, w, h)   # MSO_SHAPE_TYPE.RECTANGLE = 1
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    else:
        shape.fill.background()
    if line:
        shape.line.color.rgb = line
        shape.line.width = Pt(1.5)
    else:
        shape.line.fill.background()
    return shape


def add_text(slide, text, x, y, w, h,
             size=24, bold=False, color=WHITE, align=PP_ALIGN.LEFT,
             italic=False, wrap=True):
    txb = slide.shapes.add_textbox(x, y, w, h)
    txb.word_wrap = wrap
    tf = txb.text_frame
    tf.word_wrap = wrap
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    return txb


def dark_slide(prs):
    slide = prs.slides.add_slide(blank)
    add_rect(slide, 0, 0, W, H, fill=NAVY)
    return slide


def light_slide(prs):
    slide = prs.slides.add_slide(blank)
    add_rect(slide, 0, 0, W, H, fill=LIGHT_BG)
    return slide


def header_bar(slide, title, subtitle=None, dark=True):
    """상단 헤더 바 + 타이틀"""
    txt_color = WHITE if dark else NAVY
    add_rect(slide, 0, 0, W, Inches(1.25), fill=BLUE)
    add_text(slide, title,
             Inches(0.4), Inches(0.18), Inches(10), Inches(0.75),
             size=32, bold=True, color=WHITE)
    if subtitle:
        add_text(slide, subtitle,
                 Inches(0.4), Inches(0.78), Inches(9), Inches(0.45),
                 size=16, color=RGBColor(0xD0, 0xE8, 0xFF))


def accent_line(slide, y=Inches(1.3), dark=True):
    add_rect(slide, Inches(0.4), y, Inches(0.07), Inches(0.0),
             fill=CYAN, line=CYAN)


def bullet_box(slide, items, x, y, w, h,
               icon="▶", size=16, color=WHITE, gap=Inches(0.45)):
    """아이콘 + 텍스트 불릿 목록"""
    for i, item in enumerate(items):
        add_text(slide, icon, x, y + gap * i, Inches(0.3), gap,
                 size=size, color=CYAN, bold=True)
        add_text(slide, item, x + Inches(0.32), y + gap * i, w - Inches(0.32), gap,
                 size=size, color=color)


def card(slide, title, body_lines, x, y, w=Inches(3.8), h=Inches(2.0),
         title_color=BLUE, bg=RGBColor(0x17, 0x28, 0x55), text_color=WHITE):
    add_rect(slide, x, y, w, h, fill=bg)
    add_rect(slide, x, y, w, Inches(0.42), fill=title_color)
    add_text(slide, title, x + Inches(0.15), y + Inches(0.05), w - Inches(0.2),
             Inches(0.38), size=14, bold=True, color=WHITE)
    for i, line in enumerate(body_lines):
        add_text(slide, line, x + Inches(0.15), y + Inches(0.5 + i * 0.32),
                 w - Inches(0.2), Inches(0.32), size=12, color=text_color)


def flow_arrow(slide, x, y):
    """→ 화살표"""
    add_text(slide, "→", x, y, Inches(0.4), Inches(0.4),
             size=24, bold=True, color=CYAN, align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 1 — 표지
# ══════════════════════════════════════════════════════════════════════════════
s = dark_slide(prs)

# 배경 그라데이션 느낌 – 왼쪽 사이드바
add_rect(s, 0, 0, Inches(0.5), H, fill=BLUE)
add_rect(s, Inches(0.5), 0, Inches(0.07), H, fill=CYAN)

# 태그라인
add_text(s, "TBIT 클라우드 MSP 해커톤 2026",
         Inches(1.0), Inches(1.2), Inches(10), Inches(0.5),
         size=18, color=CYAN)

# 메인 제목
add_text(s, "AI 기반 장애 대응\n자동화 시스템",
         Inches(1.0), Inches(1.9), Inches(9.5), Inches(2.2),
         size=54, bold=True, color=WHITE)

# 부제
add_text(s, "클라우드 인프라 장애, 이제 AI가 5분 안에 분석합니다",
         Inches(1.0), Inches(4.2), Inches(9), Inches(0.6),
         size=22, color=GRAY, italic=True)

# 구분선
add_rect(s, Inches(1.0), Inches(4.95), Inches(5), Inches(0.04), fill=BLUE)

# 날짜 / 팀
add_text(s, "2026.05.08   |   TBIT MSP팀",
         Inches(1.0), Inches(5.2), Inches(6), Inches(0.45),
         size=16, color=GRAY)

# 우측 아이콘 영역 (텍스트 아트)
add_text(s, "⚡",
         Inches(10.5), Inches(2.8), Inches(1.5), Inches(1.5),
         size=80, color=RGBColor(0x1A, 0x73, 0xE8), align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 2 — 팀 소개
# ══════════════════════════════════════════════════════════════════════════════
s = dark_slide(prs)
header_bar(s, "팀 소개", "Team Introduction")

# 역할 카드 4개
roles = [
    ("PM / 기획", ["아이디어 기획", "발표 자료 작성", "전체 방향 조율"]),
    ("클라우드 아키텍트", ["NCP 인프라 설계", "Cloud Insight 연동", "Webhook 구성"]),
    ("백엔드 개발", ["FastAPI 서버 구현", "AI 분석 파이프라인", "SQLite DB 설계"]),
    ("AI / 데이터", ["Timely GPT 연동", "프롬프트 엔지니어링", "분석 포맷 설계"]),
]
xs = [Inches(0.4), Inches(3.6), Inches(6.8), Inches(10.0)]
for i, (role, duties) in enumerate(roles):
    card(s, role, duties, xs[i], Inches(1.5), w=Inches(2.9), h=Inches(2.4))

# 해커톤 진행 사진 안내 (실제 사진 삽입 위치 표시)
add_rect(s, Inches(0.4), Inches(4.1), Inches(12.5), Inches(2.9),
         fill=RGBColor(0x10, 0x22, 0x4A), line=BLUE)
add_text(s, "📸  해커톤 진행 사진",
         Inches(5.5), Inches(5.2), Inches(3), Inches(0.6),
         size=22, color=GRAY, align=PP_ALIGN.CENTER)
add_text(s, "[ 사진 삽입 영역 ]",
         Inches(5.2), Inches(5.7), Inches(3.5), Inches(0.5),
         size=14, color=RGBColor(0x50, 0x60, 0x80), align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 3 — 문제 정의
# ══════════════════════════════════════════════════════════════════════════════
s = light_slide(prs)
header_bar(s, "문제 정의", "Problem Statement", dark=False)

# 왼쪽: 현황
add_rect(s, Inches(0.4), Inches(1.4), Inches(5.8), Inches(5.6),
         fill=NAVY)
add_text(s, "🔴  현재 상황",
         Inches(0.6), Inches(1.5), Inches(5.4), Inches(0.5),
         size=18, bold=True, color=ORANGE)
problems = [
    "장애 인지 → 원인 파악까지 평균 30분 ~ 2시간",
    "야간·주말 대응 인력 부족으로 지연 심화",
    "SA 엔지니어가 수동으로 로그 확인 및 분석",
    "장애 이력이 개인 경험에 의존, 재발 방지 어려움",
    "동시 다발 알람 시 우선순위 판단 불가",
]
bullet_box(s, problems, Inches(0.6), Inches(2.1),
           Inches(5.5), Inches(4.5), icon="✗", size=15, color=GRAY)

# 오른쪽: 핵심 수치
add_rect(s, Inches(6.5), Inches(1.4), Inches(6.4), Inches(5.6),
         fill=RGBColor(0xE8, 0xF0, 0xFF))

stats = [
    ("30분~2시간", "장애 대응 소요 시간"),
    ("야간·주말", "대응 공백 발생"),
    ("수동 분석", "로그 확인 방식"),
]
for i, (num, desc) in enumerate(stats):
    yy = Inches(1.7 + i * 1.7)
    add_rect(s, Inches(6.8), yy, Inches(5.8), Inches(1.4),
             fill=WHITE, line=BLUE)
    add_text(s, num,
             Inches(7.0), yy + Inches(0.1), Inches(5.4), Inches(0.8),
             size=32, bold=True, color=BLUE, align=PP_ALIGN.CENTER)
    add_text(s, desc,
             Inches(7.0), yy + Inches(0.85), Inches(5.4), Inches(0.45),
             size=15, color=NAVY, align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 4 — 해결 방안
# ══════════════════════════════════════════════════════════════════════════════
s = dark_slide(prs)
header_bar(s, "해결 방안", "AI-Powered Incident Response Automation")

# 핵심 가치 3개
values = [
    ("⚡  5분 이내", "알람 수신 → AI 분석 완료"),
    ("🤖  AI 자동 분석", "원인 분류 + 즉시 조치 3가지 제시"),
    ("📊  이력 누적", "장애 패턴 학습 기반 재발 방지"),
]
for i, (title, desc) in enumerate(values):
    x = Inches(0.4 + i * 4.3)
    add_rect(s, x, Inches(1.5), Inches(4.0), Inches(1.5), fill=BLUE)
    add_text(s, title, x + Inches(0.2), Inches(1.6), Inches(3.6), Inches(0.6),
             size=20, bold=True, color=WHITE)
    add_text(s, desc, x + Inches(0.2), Inches(2.1), Inches(3.6), Inches(0.7),
             size=14, color=RGBColor(0xD0, 0xE8, 0xFF))

# 플로우 다이어그램
add_text(s, "처리 흐름",
         Inches(0.4), Inches(3.3), Inches(5), Inches(0.4),
         size=14, color=CYAN, bold=True)

steps = [
    ("① 알람 수신", "Cloud Insight\nWebhook"),
    ("② 로그 수집", "알람 시점 ±15분\n자동 수집"),
    ("③ AI 분석", "GPT 원인 분류\n+ 조치 권고"),
    ("④ 대시보드", "결과 즉시\n웹 확인"),
]
step_xs = [Inches(0.4), Inches(3.5), Inches(6.6), Inches(9.7)]
for i, (title, desc) in enumerate(steps):
    bx = step_xs[i]
    add_rect(s, bx, Inches(3.8), Inches(2.8), Inches(2.8),
             fill=RGBColor(0x17, 0x28, 0x55), line=CYAN)
    add_rect(s, bx, Inches(3.8), Inches(2.8), Inches(0.45), fill=CYAN)
    add_text(s, title, bx + Inches(0.1), Inches(3.85),
             Inches(2.6), Inches(0.38),
             size=13, bold=True, color=NAVY)
    add_text(s, desc, bx + Inches(0.15), Inches(4.35),
             Inches(2.5), Inches(2.0),
             size=13, color=WHITE)
    if i < 3:
        flow_arrow(s, step_xs[i] + Inches(2.85), Inches(4.85))


# ══════════════════════════════════════════════════════════════════════════════
# Slide 5 — 시스템 아키텍처
# ══════════════════════════════════════════════════════════════════════════════
s = dark_slide(prs)
header_bar(s, "시스템 아키텍처", "System Architecture")

# 기술 스택 뱃지 (우상단)
stack = ["Python 3.12", "FastAPI", "SQLite", "NCP Cloud Insight", "Timely GPT"]
for i, tech in enumerate(stack):
    add_rect(s, Inches(7.5 + (i % 3) * 1.8), Inches(1.4 + (i // 3) * 0.45),
             Inches(1.65), Inches(0.38), fill=RGBColor(0x17, 0x28, 0x55), line=BLUE)
    add_text(s, tech,
             Inches(7.55 + (i % 3) * 1.8), Inches(1.44 + (i // 3) * 0.45),
             Inches(1.5), Inches(0.35),
             size=11, color=CYAN, align=PP_ALIGN.CENTER)

# 아키텍처 다이어그램
boxes = [
    (Inches(0.3),  Inches(2.5), "☁ NCP\nCloud Insight", BLUE),
    (Inches(2.7),  Inches(2.5), "🔗 Webhook\nReceiver",  RGBColor(0x17, 0x28, 0x55)),
    (Inches(5.1),  Inches(2.5), "📦 Collector\n(Mock/NCP CLA)", RGBColor(0x17, 0x28, 0x55)),
    (Inches(7.5),  Inches(2.5), "🤖 AI Analyzer\n(Timely GPT)", RGBColor(0x17, 0x28, 0x55)),
    (Inches(9.9),  Inches(2.5), "🗄 DB\n(SQLite)", RGBColor(0x17, 0x28, 0x55)),
]
for (bx, by, label, bg) in boxes:
    add_rect(s, bx, by, Inches(2.2), Inches(1.3), fill=bg, line=BLUE)
    add_text(s, label, bx + Inches(0.1), by + Inches(0.15),
             Inches(2.0), Inches(1.0), size=13, color=WHITE, align=PP_ALIGN.CENTER)

# 화살표
for i in range(len(boxes) - 1):
    flow_arrow(s, boxes[i][0] + Inches(2.25), Inches(2.95))

# 아래: DB → 웹 대시보드
add_rect(s, Inches(4.5), Inches(5.0), Inches(4.3), Inches(1.2),
         fill=BLUE, line=CYAN)
add_text(s, "🖥  웹 대시보드\n장애 목록 · 상세 · AI 분석 결과 조회",
         Inches(4.7), Inches(5.1), Inches(3.9), Inches(1.0),
         size=14, bold=True, color=WHITE, align=PP_ALIGN.CENTER)

# DB → Dashboard 화살표
add_text(s, "↓", Inches(6.3), Inches(4.0), Inches(0.5), Inches(0.8),
         size=22, bold=True, color=CYAN, align=PP_ALIGN.CENTER)

# NCP 폴러 설명
add_rect(s, Inches(0.3), Inches(4.4), Inches(2.2), Inches(0.8),
         fill=RGBColor(0x10, 0x22, 0x4A), line=GRAY)
add_text(s, "NCP Poller\n(60초 주기 폴링)", Inches(0.35), Inches(4.45),
         Inches(2.1), Inches(0.7), size=10, color=GRAY, align=PP_ALIGN.CENTER)
add_text(s, "↓", Inches(1.25), Inches(3.9), Inches(0.3), Inches(0.4),
         size=14, bold=True, color=GRAY, align=PP_ALIGN.CENTER)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 6 — 데모
# ══════════════════════════════════════════════════════════════════════════════
s = light_slide(prs)
header_bar(s, "데모 시연", "Live Demo — AI Incident Response", dark=False)

# 시나리오 스텝
steps_demo = [
    ("STEP 1", "테스트 알람 발생\n/test/trigger API 호출"),
    ("STEP 2", "대시보드에서\n'processing' 상태 확인"),
    ("STEP 3", "AI 분석 완료\n자동 결과 업데이트"),
    ("STEP 4", "장애 상세 페이지\n원인·조치 권고 확인"),
]
for i, (step, desc) in enumerate(steps_demo):
    bx = Inches(0.3 + i * 3.25)
    add_rect(s, bx, Inches(1.5), Inches(3.0), Inches(2.0), fill=NAVY)
    add_rect(s, bx, Inches(1.5), Inches(3.0), Inches(0.4), fill=BLUE)
    add_text(s, step, bx + Inches(0.1), Inches(1.52), Inches(2.8), Inches(0.38),
             size=13, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, desc, bx + Inches(0.1), Inches(2.0), Inches(2.8), Inches(1.3),
             size=13, color=GRAY, align=PP_ALIGN.CENTER)
    if i < 3:
        add_text(s, "→", bx + Inches(3.05), Inches(2.1), Inches(0.2), Inches(0.4),
                 size=20, bold=True, color=BLUE, align=PP_ALIGN.CENTER)

# AI 분석 결과 샘플 박스
add_rect(s, Inches(0.3), Inches(3.8), Inches(12.7), Inches(3.2),
         fill=NAVY, line=BLUE)
add_text(s, "AI 분석 결과 샘플",
         Inches(0.5), Inches(3.9), Inches(5), Inches(0.4),
         size=14, bold=True, color=CYAN)

sample = [
    ("원인 분류", "리소스 부족 (CPU 포화)"),
    ("심각도", "Critical"),
    ("영향 범위", "web-01 서버 전체 서비스 지연"),
    ("즉시 조치", "① top 명령으로 고부하 프로세스 확인  ② kill -9 {PID}  ③ Auto Scaling 트리거"),
    ("재발 방지", "CPU 임계값 알람 75%로 하향, 수평 확장 정책 수립"),
    ("신뢰도", "높음"),
]
for i, (k, v) in enumerate(sample):
    col = 0 if i < 3 else 1
    row = i if i < 3 else i - 3
    cx = Inches(0.5 + col * 6.5)
    cy = Inches(4.4 + row * 0.6)
    add_text(s, f"  {k}", cx, cy, Inches(1.8), Inches(0.52),
             size=11, bold=True, color=CYAN)
    add_text(s, v, cx + Inches(1.8), cy, Inches(4.5), Inches(0.52),
             size=11, color=WHITE)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 7 — 기대 효과
# ══════════════════════════════════════════════════════════════════════════════
s = dark_slide(prs)
header_bar(s, "기대 효과", "Expected Impact")

effects = [
    ("⏱  대응 시간 단축", "30분~2시간  →  5분 이내",
     ["알람 수신 즉시 AI 분석 시작", "엔지니어 수동 조사 시간 90% 절감", "야간·주말도 동일한 대응 품질 유지"]),
    ("💰  운영 비용 절감", "불필요한 에스컬레이션 감소",
     ["1차 원인 파악 자동화로 SA 부담 감소", "장애 이력 누적으로 재발 방지", "On-call 야간 출동 최소화"]),
    ("📈  고객 신뢰 향상", "MSP 서비스 품질 차별화",
     ["SLA 위반 리스크 감소", "장애 리포트 자동 생성 가능", "AI 기반 선제적 대응 경쟁력 확보"]),
]
for i, (title, subtitle, bullets) in enumerate(effects):
    bx = Inches(0.35 + i * 4.35)
    add_rect(s, bx, Inches(1.5), Inches(4.1), Inches(5.5),
             fill=RGBColor(0x10, 0x20, 0x4A), line=BLUE)
    add_rect(s, bx, Inches(1.5), Inches(4.1), Inches(0.85), fill=BLUE)
    add_text(s, title, bx + Inches(0.1), Inches(1.55), Inches(3.9), Inches(0.5),
             size=16, bold=True, color=WHITE)
    add_text(s, subtitle, bx + Inches(0.1), Inches(2.05), Inches(3.9), Inches(0.35),
             size=12, color=CYAN, italic=True)
    for j, b in enumerate(bullets):
        add_text(s, "• " + b,
                 bx + Inches(0.15), Inches(2.55 + j * 0.52),
                 Inches(3.8), Inches(0.48),
                 size=13, color=GRAY)


# ══════════════════════════════════════════════════════════════════════════════
# Slide 8 — 향후 발전 방향
# ══════════════════════════════════════════════════════════════════════════════
s = light_slide(prs)
header_bar(s, "향후 발전 방향", "Roadmap & Commercialization", dark=False)

phases = [
    ("Phase 1\n(완료)", "MVP", [
        "Webhook 알람 수신",
        "AI 원인 분석 (Timely GPT)",
        "웹 대시보드",
        "NCP Cloud Insight 폴링",
    ], GREEN),
    ("Phase 2\n(단기)", "알림 & 자동화", [
        "Slack 알림 연동",
        "Claude API 교체 (고성능)",
        "Auto Scaling 자동 트리거",
        "장애 리포트 자동 생성",
    ], BLUE),
    ("Phase 3\n(중장기)", "지능화 & 사업화", [
        "유사 장애 벡터 검색",
        "멀티 고객사 계정 관리",
        "예측형 장애 감지 (이상 탐지)",
        "SaaS 서비스화 · MSP 패키지",
    ], ORANGE),
]
for i, (phase, title, items, color) in enumerate(phases):
    bx = Inches(0.35 + i * 4.35)
    add_rect(s, bx, Inches(1.5), Inches(4.1), Inches(5.5),
             fill=NAVY)
    add_rect(s, bx, Inches(1.5), Inches(4.1), Inches(1.0), fill=color)
    add_text(s, phase, bx + Inches(0.1), Inches(1.55), Inches(3.9), Inches(0.55),
             size=13, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text(s, title, bx + Inches(0.1), Inches(2.6), Inches(3.9), Inches(0.5),
             size=17, bold=True, color=color)
    for j, item in enumerate(items):
        add_text(s, "▶ " + item,
                 bx + Inches(0.15), Inches(3.2 + j * 0.52),
                 Inches(3.8), Inches(0.48),
                 size=13, color=WHITE)

# 사업화 한 줄 요약
add_rect(s, Inches(0.35), Inches(7.0), Inches(12.7), Inches(0.4),
         fill=BLUE)
add_text(s,
         "NCP MSP 환경에 최적화된 AI 장애 대응 솔루션 → 사내 도입 후 MSP 고객사 패키지 서비스로 확장",
         Inches(0.5), Inches(7.02), Inches(12.3), Inches(0.36),
         size=13, color=WHITE, align=PP_ALIGN.CENTER)


# ── 저장 ─────────────────────────────────────────────────────────────────────
out = "AI_Incident_Response_발표.pptx"
prs.save(out)
print(f"[OK] 저장 완료: {out}")
print(f"   슬라이드 수: {len(prs.slides)}")
