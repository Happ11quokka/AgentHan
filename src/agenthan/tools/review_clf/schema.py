"""네이버 플레이스 방문자 리뷰 [특징] 필터와 동일한 16개 클래스 및 출력 스키마."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# 네이버 방문자 리뷰 탭 [특징] 필터 순서 그대로
FEATURES: list[str] = [
    "맛",
    "만족도",
    "서비스",
    "음식량",
    "가격",
    "분위기",
    "메뉴",
    "대기시간",
    "청결도",
    "목적",
    "위치",
    "예약",
    "전망",
    "배달",
    "방역",
    "주차",
]

# 조사 데이터에서 확인한 '실제' 범위.
# 이름이 아니라 네이버가 실제로 붙이는 범위를 기준으로 적었다 (LLM 프롬프트에 사용).
FEATURE_GUIDE: dict[str, str] = {
    "맛": "음식·음료의 맛 평가 (맛있어요, 맛집, 짜요, 느끼해요)",
    "만족도": "전반적 호감·재방문 의사·추천 (좋았어요, 다음에 또 올게요, 최고, 잘 먹고 갑니다)",
    "서비스": "직원·사장님의 친절/불친절, 응대 태도",
    "음식량": "양이 많다/적다, 푸짐함, 배부름, 사이즈",
    "가격": "가격 수준, 가성비, 비싸다/저렴하다, 가격 대비 구성",
    "분위기": "매장 분위기, 인테리어, 넓이, 조용함·시끄러움, 혼밥하기 좋음",
    "메뉴": "메뉴 종류·다양성, 세트·런치·사이드 메뉴 구성",
    "대기시간": "웨이팅/줄서기뿐 아니라 음식이 나오는 속도 (음식이 빨리 나와요, 대기 없이 입장)",
    "청결도": "청결·위생, 깨끗함, 화장실, 냄새",
    "목적": "누구와/어떤 상황에 방문 (부모님 모시고, 가족끼리, 데이트, 모임, ~먹고 싶을 때)",
    "위치": "위치·접근성 (회사 근처, 역에서 가까움, 찾기 어려움)",
    "예약": "예약뿐 아니라 주문·결제 방식 (예약하고 방문, 테이블 주문, 페이 결제 가능)",
    "전망": "뷰·전망, 창밖 풍경, 야경",
    "배달": "포장·배달·픽업 (포장해서 먹었는데, 배달이 빨라요)",
    "방역": "테이블·좌석 간격, 환기, 마스크",
    "주차": "주차 가능 여부, 주차장, 주차비",
}


@dataclass
class Span:
    start: int  # 리뷰 원문 기준 문자 오프셋 (inclusive)
    end: int  # exclusive
    text: str


@dataclass
class FeatureResult:
    feature: str
    highlights: list[Span] = field(default_factory=list)
    score: float | None = None


@dataclass
class ClassifyResult:
    """네이버 리뷰 탭과 같은 형태: 리뷰 한 건 → 특징 목록 + 각 특징의 굵게 표시 구간."""

    text: str
    features: list[FeatureResult] = field(default_factory=list)

    @property
    def labels(self) -> list[str]:
        return [f.feature for f in self.features]

    def to_dict(self) -> dict:
        return asdict(self)
