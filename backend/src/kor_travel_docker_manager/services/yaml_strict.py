"""중복 키를 거부하는 YAML 로더 — registry.py와 compose_service.py가 공유한다.

PyYAML의 기본 `safe_load`는 매핑에 같은 키가 두 번 있으면 조용히 뒤엣것으로
덮어쓴다. `docker-targets.yml`이나 Map source manifest처럼 사람이 손으로 편집하는
설정 파일에서는 중복 키 자체가 실수 신호이고, 조용한 override는 diff로 보기 전엔
발견하기 어렵다.

`registry.py`가 이 로더를 직접 두지 않고 여기서 가져오는 이유: compose_service.py가
이미 registry.py를 import하므로, registry.py가 compose_service.py의 로더를
역으로 import하면 순환 import가 된다.
"""

from __future__ import annotations

from typing import Any

import yaml


class UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_yaml_mapping(
    loader: UniqueKeySafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_yaml_mapping,
)


def load_yaml_rejecting_duplicate_keys(source: str) -> Any:
    """`source`(YAML 텍스트)를 파싱하되 중복 매핑 키를 fail-close로 거부한다."""

    loader = UniqueKeySafeLoader(source)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
