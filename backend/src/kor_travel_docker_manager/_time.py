import datetime


def utcnow() -> datetime.datetime:
    """현재 UTC 시각을 naive datetime으로 반환한다.

    ``datetime.datetime.utcnow()`` 는 Python 3.12+ 에서 deprecated 이며 향후 제거 예정이다.
    DB 컬럼(``DateTime``)은 naive UTC 값을 저장하므로, 비교 시 naive/aware 혼용으로 인한
    ``TypeError`` 를 피하기 위해 timezone 정보를 제거한 naive 값을 반환한다.
    """
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
