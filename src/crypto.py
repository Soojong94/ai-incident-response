"""대칭 암호화 모듈 — site별 NCP/CF 키 저장에 사용.

- 알고리즘: Fernet (AES-128-CBC + HMAC, cryptography 라이브러리 표준)
- 키: settings.encryption_key (.env의 ENCRYPTION_KEY, 32-byte url-safe base64)
- 미설정 시 경고 + 메모리상 휘발 키 생성 (재시작하면 기존 암호문 복호화 불가 → 운영 부적합)
"""
import logging

from cryptography.fernet import Fernet, InvalidToken

from src.config import settings

logger = logging.getLogger(__name__)

_FERNET: Fernet | None = None
_USING_EPHEMERAL = False


def _get_fernet() -> Fernet:
    global _FERNET, _USING_EPHEMERAL
    if _FERNET is not None:
        return _FERNET
    key = settings.encryption_key.strip() if settings.encryption_key else ""
    if not key:
        # 휘발 키 — 재시작 시 모든 암호문 무효. dev 전용. 운영에선 절대 사용 금지.
        ephemeral = Fernet.generate_key()
        _FERNET = Fernet(ephemeral)
        _USING_EPHEMERAL = True
        logger.warning(
            "[보안 경고] ENCRYPTION_KEY 미설정 — 휘발 키로 동작. "
            "site에 저장한 API 키는 서버 재시작 시 복호화 불가. "
            "운영 배포 전 'python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"'로 발급해 .env에 등록하세요."
        )
    else:
        try:
            _FERNET = Fernet(key.encode())
        except Exception as e:
            raise RuntimeError(f"ENCRYPTION_KEY 형식 오류 — Fernet.generate_key() 결과여야 합니다: {e}") from e
    return _FERNET


def is_ephemeral() -> bool:
    """현재 키가 휘발 키인지 (운영 부적합 여부)."""
    _get_fernet()
    return _USING_EPHEMERAL


def encrypt(plain: str) -> str:
    """평문 → 암호문 (urlsafe base64). 빈 입력은 빈 출력 반환."""
    if not plain:
        return ""
    return _get_fernet().encrypt(plain.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """암호문 → 평문. 잘못된 토큰이면 빈 문자열 반환 (로그만 남김)."""
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.warning("암호문 복호화 실패 — 키 변경 또는 데이터 손상")
        return ""
    except Exception as e:
        logger.error("복호화 예외: %s", e)
        return ""


def mask(plain: str, visible: int = 4) -> str:
    """UI 표시용 마스킹. 'abcdef1234' → '••••••1234'."""
    if not plain:
        return ""
    if len(plain) <= visible:
        return "•" * len(plain)
    return "•" * (len(plain) - visible) + plain[-visible:]
