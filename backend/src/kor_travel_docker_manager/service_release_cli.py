"""현재 서비스 release의 명시 등록·조회 CLI. 앱을 빌드하거나 교체하지 않는다."""
from __future__ import annotations

import argparse
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import docker

from kor_travel_docker_manager.services.c6c_deployment import (
    DeploymentContractError,
    manager_mutation_lock,
)
from kor_travel_docker_manager.services.registry import (
    current_host,
    load_targets_config,
    target_hosts,
)
from kor_travel_docker_manager.services.service_release import (
    ServiceReleaseError,
    adopt_release,
    read_current_release,
    verify_release,
)

DEFAULT_RELEASE_ROOT = Path('/srv/ktdm-releases')


def _declaration(service: str, config: dict[str, Any]) -> dict[str, Any]:
    declarations = config.get('service_releases', {})
    if service not in declarations or service not in config['targets']:
        raise ServiceReleaseError('등록되지 않은 서비스 release입니다')
    declaration = declarations[service]
    if not isinstance(declaration, dict) or set(declaration) != {'groups'}:
        raise ServiceReleaseError('release catalog 형태가 올바르지 않습니다')
    if current_host(config) not in target_hosts(service, config):
        raise ServiceReleaseError('현재 호스트가 이 release의 소유 호스트와 다릅니다')
    groups = declaration['groups']
    if not isinstance(groups, dict) or not groups:
        raise ServiceReleaseError('release Compose 그룹이 필요합니다')
    declaration = deepcopy(declaration)
    groups = declaration['groups']
    for group in groups.values():
        if isinstance(group, dict) and group.get('external_project') is True:
            external = config['targets'][service].get('external_project')
            if not isinstance(external, dict) or not {'project', 'working_dir'} <= set(external):
                raise ServiceReleaseError('정본 external project 좌표가 없습니다')
            group.pop('external_project')
            if 'project' in group or 'working_dir' in group:
                raise ServiceReleaseError('external project 좌표를 중복 선언할 수 없습니다')
            group['project'] = external['project']
            group['working_dir'] = external['working_dir']
    roles: set[str] = set()
    fields = {'project', 'working_dir', 'config_files', 'env_file', 'runtime_services', 'oneshot_services'}
    for group in groups.values():
        if not isinstance(group, dict) or set(group) != fields:
            raise ServiceReleaseError('release Compose 그룹 선언이 올바르지 않습니다')
        runtime, oneshot = group['runtime_services'], group['oneshot_services']
        if not isinstance(runtime, list) or not isinstance(oneshot, list) or not (runtime or oneshot):
            raise ServiceReleaseError('release 서비스 역할이 필요합니다')
        names = runtime + oneshot
        if any(not isinstance(name, str) or not name for name in names):
            raise ServiceReleaseError('release 서비스 이름이 올바르지 않습니다')
        if len(set(names)) != len(names) or roles.intersection(names):
            raise ServiceReleaseError('release 서비스 역할이 중복되었습니다')
        roles.update(names)
    return declaration


def _profile(service: str, declaration: dict[str, Any], entries: list[str]) -> dict[str, Any]:
    explicit: dict[str, str] = {}
    for entry in entries:
        name, separator, value = entry.partition('=')
        if separator != '=' or name in explicit:
            raise ServiceReleaseError('one-shot 이미지 지정이 없거나 중복되었습니다')
        explicit[name] = value
    expected = {name for group in declaration['groups'].values() for name in group['oneshot_services']}
    if set(explicit) != expected:
        raise ServiceReleaseError('catalog의 모든 one-shot 이미지 ID를 빠짐없이 명시해야 합니다')
    groups = {}
    for key, group in declaration['groups'].items():
        groups[key] = deepcopy(group)
        groups[key].pop('oneshot_services')
        groups[key]['oneshot_images'] = {name: explicit[name] for name in group['oneshot_services']}
    return {'service': service, 'groups': groups}


def _check_roles(manifest: dict[str, Any], declaration: dict[str, Any]) -> None:
    if set(manifest['groups']) != set(declaration['groups']):
        raise ServiceReleaseError('현재 release와 catalog의 Compose 그룹이 다릅니다')
    for key, group in declaration['groups'].items():
        actual = manifest['groups'][key]
        if (set(actual['runtime_services']) != set(group['runtime_services'])
                or set(actual['oneshot_images']) != set(group['oneshot_services'])
                or actual['project'] != group['project']
                or actual['working_dir'] != group['working_dir']):
            raise ServiceReleaseError('현재 release와 catalog의 역할·좌표가 다릅니다')
        base = Path(group['working_dir'])
        files = [str(Path(f) if Path(f).is_absolute() else base / f) for f in group['config_files']]
        env = group['env_file']
        env = str(Path(env) if Path(env).is_absolute() else base / env) if env is not None else None
        if actual['config_files'] != files or actual['env_file'] != env:
            raise ServiceReleaseError('현재 release와 catalog의 Compose 파일이 다릅니다')


def compose_arguments(service: str, group_name: str, *, root: Path, config: dict[str, Any]) -> dict[str, Any]:
    """고정 pointer를 한 번씩 읽어 같은 project의 모든 overlay 인자를 만든다."""
    declaration = _declaration(service, config)
    if group_name not in declaration['groups']:
        raise ServiceReleaseError('등록되지 않은 Compose 그룹입니다')
    own = read_current_release(service, root=root)
    _check_roles(own, declaration)
    selected = own['groups'][group_name]
    project = selected['project']
    argv = ['docker', 'compose', '--project-name', project,
            '--project-directory', selected['working_dir']]
    if selected['env_file'] is not None:
        argv += ['--env-file', selected['env_file']]
    for compose_file in selected['config_files']:
        argv += ['-f', compose_file]
    owners: dict[str, str] = {}
    overlays = []
    manifests = {service: own}
    for sibling in sorted(config.get('service_releases', {})):
        if current_host(config) not in target_hosts(sibling, config):
            continue
        sibling_declaration = _declaration(sibling, config)
        matching = [group for group in sibling_declaration['groups'].values() if group['project'] == project]
        if not matching:
            continue
        if any(group['working_dir'] != selected['working_dir'] for group in matching):
            raise ServiceReleaseError('같은 Compose project의 working_dir가 다릅니다')
        manifest = manifests.get(sibling)
        if manifest is None:
            manifest = read_current_release(sibling, root=root)
            _check_roles(manifest, sibling_declaration)
            manifests[sibling] = manifest
        for group in manifest['groups'].values():
            if group['project'] != project:
                raise ServiceReleaseError('한 release의 서로 다른 project overlay는 분리 등록이 필요합니다')
            for name in group['runtime_services'] + list(group['oneshot_images']):
                if name in owners:
                    raise ServiceReleaseError('같은 Compose 서비스의 release 소유자가 중복되었습니다')
                owners[name] = sibling
        overlay = str(Path(manifest['release_directory']) / 'images.yml')
        overlays.append(overlay)
        argv += ['-f', overlay]
    return {'service': service, 'group': group_name, 'project': project,
            'argv': argv, 'runtime_services': selected['runtime_services'], 'overlays': overlays}


def _run(args: argparse.Namespace) -> int:
    if args.release_action == 'adopt' and not args.confirm:
        print(json.dumps({'success': False, 'message': '등록하려면 --confirm이 필요합니다'}, ensure_ascii=False))
        return 2
    try:
        config = load_targets_config()
        declaration = _declaration(args.service, config)
        root = Path(args.root)
        if args.release_action == 'compose-args':
            payload = compose_arguments(args.service, args.group, root=root, config=config)
        elif args.release_action == 'adopt':
            if os.geteuid() != 0:
                raise ServiceReleaseError('release 등록은 root로 실행해야 합니다')
            profile = _profile(args.service, declaration, args.oneshot_image)
            # 생성자의 Docker 조회도 기존 G를 획득한 뒤 수행한다. 내부 G는 기존 재진입 계약을 쓴다.
            with manager_mutation_lock():
                client = docker.from_env(timeout=10)
                try:
                    payload = adopt_release(profile, root=root, client=client,
                                            replace_current=args.replace_current)
                finally:
                    client.close()
        else:
            manifest = read_current_release(args.service, root=root)
            _check_roles(manifest, declaration)
            if args.release_action == 'show':
                payload = manifest
            else:
                client = docker.from_env(timeout=10)
                try:
                    payload = verify_release(args.service, root=root, client=client)
                finally:
                    client.close()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if payload.get('result') == 'drift' else 0
    except (ServiceReleaseError, DeploymentContractError) as exc:
        print(json.dumps({'success': False, 'message': str(exc)}, ensure_ascii=False))
        return 2
    except docker.errors.DockerException as exc:
        print(json.dumps({'success': False, 'message': 'Docker 조회에 실패했습니다',
                          'error_type': type(exc).__name__}, ensure_ascii=False))
        return 2


def add_service_release_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser('service-release', help='현재 서비스 이미지의 등록·조회·차이를 확인합니다.')
    actions = parser.add_subparsers(dest='release_action', required=True)
    for action in ('show', 'verify', 'adopt', 'compose-args'):
        child = actions.add_parser(action)
        child.add_argument('service')
        child.add_argument('--root', default=str(DEFAULT_RELEASE_ROOT))
        child.add_argument('--json', action='store_true', help='JSON 출력(기본값)')
        if action == 'adopt':
            child.add_argument('--confirm', action='store_true')
            child.add_argument('--replace-current', action='store_true', help='현재 실행 이미지를 명시 재등록합니다.')
            child.add_argument('--oneshot-image', action='append', default=[], metavar='SERVICE=sha256:ID')
        if action == 'compose-args':
            child.add_argument('--group', default='main')
        child.set_defaults(func=_run)
