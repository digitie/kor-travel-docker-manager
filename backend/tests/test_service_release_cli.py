"""서비스 release CLI의 역할 완전성·잠금·공유 project 인자 회귀."""
from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kor_travel_docker_manager import cli
from kor_travel_docker_manager import service_release_cli as entry
from kor_travel_docker_manager.services.c6c_deployment import ManagerMutationActiveError
from kor_travel_docker_manager.services.registry import load_targets_config
from kor_travel_docker_manager.services.service_release import ServiceReleaseError

ID = 'sha256:' + 'a' * 64


@pytest.fixture
def declaration():
    return {'groups': {'main': {'project': 'shared', 'working_dir': '/srv/apps',
            'config_files': ['compose.yml'], 'env_file': None,
            'runtime_services': ['api', 'web'], 'oneshot_services': ['migrate']}}}


@pytest.fixture
def config(declaration, monkeypatch):
    result = {'targets': {'app': {}}, 'service_releases': {'app': declaration}}
    monkeypatch.setattr(entry, 'current_host', lambda config: 'host')
    monkeypatch.setattr(entry, 'target_hosts', lambda service, config: frozenset({'host'}))
    monkeypatch.setattr(entry, 'load_targets_config', lambda: result)
    return result


def manifest(declaration, service='app'):
    profile = entry._profile(service, declaration, ['migrate=' + ID])
    profile['groups']['main']['config_files'] = ['/srv/apps/compose.yml']
    return {**profile, 'release_directory': '/srv/releases/' + service + '-one'}


def test_catalog_uses_actual_app_roles_not_lifecycle_closure(monkeypatch):
    monkeypatch.setenv('KTDM_HOST', 'odroid')
    config = load_targets_config()
    expected = {
        'geo': {'kor-travel-geo-api', 'kor-travel-geo-ui', 'kor-travel-geo-dagster-code-server'},
        'weather': {'kor-travel-weather-api', 'kor-travel-weather-web', 'kor-travel-weather-dagster-code-server', 'kor-travel-weather-migrate'},
        'transport': {'backend', 'frontend', 'dagster-code-server', 'migrate', 'transport-admin-web', 'transport-api-gateway'},
    }
    for name, roles in expected.items():
        declaration = entry._declaration(name, config)
        actual = {role for group in declaration['groups'].values()
                  for role in group['runtime_services'] + group['oneshot_services']}
        assert actual == roles
    assert {g['project'] for g in entry._declaration('transport', config)['groups'].values()} == {'kor-travel-transport'}


def test_host_is_derived_from_target_not_copied(config, monkeypatch):
    monkeypatch.setattr(entry, 'target_hosts', lambda service, config: frozenset({'other'}))
    with pytest.raises(ServiceReleaseError, match='호스트'):
        entry._declaration('app', config)


@pytest.mark.parametrize('entries', [[], ['migrate=' + ID, 'migrate=' + ID], ['extra=' + ID], ['migrate']])
def test_oneshot_ids_must_be_explicit_and_complete(declaration, entries):
    with pytest.raises(ServiceReleaseError):
        entry._profile('app', declaration, entries)


def test_stopped_or_shared_roles_cannot_silently_disappear(config, declaration):
    current = manifest(declaration)
    current['groups']['main']['runtime_services'].remove('web')
    with pytest.raises(ServiceReleaseError, match='역할'):
        entry._check_roles(current, declaration)


def test_compose_arguments_include_all_shared_project_pointers(config, declaration, monkeypatch):
    other = deepcopy(declaration)
    other['groups']['main']['runtime_services'] = ['another-api']
    other['groups']['main']['oneshot_services'] = []
    config['targets']['other'] = {}
    config['service_releases']['other'] = other
    current = manifest(declaration)
    other_current = entry._profile('other', other, [])
    other_current['groups']['main']['config_files'] = ['/srv/apps/compose.yml']
    other_current['release_directory'] = '/srv/releases/other-one'
    observed = []
    def read(service, **kwargs):
        observed.append(service)
        return current if service == 'app' else other_current
    monkeypatch.setattr(entry, 'read_current_release', read)
    result = entry.compose_arguments('app', 'main', root=Path('/srv/releases'), config=config)
    assert result['overlays'] == ['/srv/releases/app-one/images.yml', '/srv/releases/other-one/images.yml']
    assert observed == ['app', 'other']
    assert result['runtime_services'] == ['api', 'web']
    assert result['argv'][:6] == ['docker', 'compose', '--project-name', 'shared', '--project-directory', '/srv/apps']


def test_missing_shared_pointer_does_not_fallback_to_base(config, declaration, monkeypatch):
    config['targets']['other'] = {}
    config['service_releases']['other'] = deepcopy(declaration)
    def read(service, **kwargs):
        if service == 'other':
            raise ServiceReleaseError('표지 없음')
        return manifest(declaration)
    monkeypatch.setattr(entry, 'read_current_release', read)
    with pytest.raises(ServiceReleaseError, match='표지 없음'):
        entry.compose_arguments('app', 'main', root=Path('/srv/releases'), config=config)


def test_adopt_without_confirm_makes_no_config_or_docker_call(monkeypatch, capsys):
    config = Mock(side_effect=AssertionError('config lookup'))
    monkeypatch.setattr(entry, 'load_targets_config', config)
    parser = cli.build_parser()
    args = parser.parse_args(['service-release', 'adopt', 'app'])
    assert args.func(args) == 2
    assert 'confirm' in json.loads(capsys.readouterr().out)['message']
    config.assert_not_called()


def test_adopt_acquires_g_before_docker_constructor(config, monkeypatch, capsys):
    monkeypatch.setattr(entry.os, 'geteuid', lambda: 0)
    state = []
    @contextmanager
    def lock():
        state.append('G')
        yield
    client = SimpleNamespace(close=lambda: state.append('close'))
    def connect(**kwargs):
        assert state == ['G']
        state.append('Docker')
        return client
    monkeypatch.setattr(entry, 'manager_mutation_lock', lock)
    monkeypatch.setattr(entry.docker, 'from_env', connect)
    monkeypatch.setattr(entry, 'adopt_release', lambda *a, **k: {'service': 'app'})
    args = cli.build_parser().parse_args(['service-release', 'adopt', 'app', '--confirm', '--oneshot-image', 'migrate=' + ID])
    assert args.func(args) == 0
    assert state == ['G', 'Docker', 'close']
    assert json.loads(capsys.readouterr().out)['service'] == 'app'


def test_g_contention_makes_no_docker_call(config, monkeypatch, capsys):
    monkeypatch.setattr(entry.os, 'geteuid', lambda: 0)
    @contextmanager
    def busy():
        raise ManagerMutationActiveError('another Manager mutation is active')
        yield
    monkeypatch.setattr(entry, 'manager_mutation_lock', busy)
    factory = Mock(side_effect=AssertionError('Docker touched'))
    monkeypatch.setattr(entry.docker, 'from_env', factory)
    args = cli.build_parser().parse_args(['service-release', 'adopt', 'app', '--confirm', '--oneshot-image', 'migrate=' + ID])
    assert args.func(args) == 2
    factory.assert_not_called()
    assert json.loads(capsys.readouterr().out)['success'] is False


def test_show_needs_neither_g_nor_docker(config, declaration, monkeypatch, capsys):
    monkeypatch.setattr(entry, 'read_current_release', lambda *a, **k: manifest(declaration))
    monkeypatch.setattr(entry.docker, 'from_env', Mock(side_effect=AssertionError('Docker')))
    monkeypatch.setattr(entry, 'manager_mutation_lock', Mock(side_effect=AssertionError('G')))
    args = cli.build_parser().parse_args(['service-release', 'show', 'app'])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)['service'] == 'app'


def test_external_coordinates_are_derived_once(config, declaration):
    group = declaration['groups']['main']
    group.pop('project')
    group.pop('working_dir')
    group['external_project'] = True
    config['targets']['app']['external_project'] = {'project': 'canonical', 'working_dir': '/srv/source'}
    resolved = entry._declaration('app', config)
    assert resolved['groups']['main']['project'] == 'canonical'
    assert resolved['groups']['main']['working_dir'] == '/srv/source'
    assert 'project' not in group
    group['working_dir'] = '/srv/second-source'
    with pytest.raises(ServiceReleaseError, match='중복 선언'):
        entry._declaration('app', config)
