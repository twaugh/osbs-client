"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import copy
import json
import os
from pkg_resources import parse_version
import shutil

from osbs.build.build_request import BuildManager, BuildRequest, ProductionBuild
from osbs.constants import (PROD_BUILD_TYPE, PROD_WITHOUT_KOJI_BUILD_TYPE,
                            PROD_WITH_SECRET_BUILD_TYPE)
from osbs.exceptions import OsbsValidationException

from flexmock import flexmock
import pytest

from tests.constants import (INPUTS_PATH, TEST_BUILD_CONFIG, TEST_BUILD_JSON, TEST_COMPONENT,
                             TEST_GIT_BRANCH, TEST_GIT_REF, TEST_GIT_URI)


class NoSuchPluginException(Exception):
    pass


def get_plugin(plugins, plugin_type, plugin_name):
    plugins = plugins[plugin_type]
    for plugin in plugins:
        if plugin["name"] == plugin_name:
            return plugin
    else:
        raise NoSuchPluginException()


def plugin_value_get(plugins, plugin_type, plugin_name, *args):
    result = get_plugin(plugins, plugin_type, plugin_name)
    for arg in args:
        result = result[arg]
    return result


class TestBuildRequest(object):
    def test_build_request_is_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.is_auto_instantiated() is True

    def test_build_request_isnt_auto_instantiated(self):
        build_json = copy.deepcopy(TEST_BUILD_JSON)
        build_json['spec']['triggers'] = []
        br = BuildRequest('something')
        flexmock(br).should_receive('template').and_return(build_json)
        assert br.is_auto_instantiated() is False

    def test_render_simple_request_incorrect_postbuild(self, tmpdir):
        # Make temporary copies of the JSON files
        for basename in ['simple.json', 'simple_inner.json']:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(str(tmpdir), basename))

        # Create an inner JSON description which incorrectly runs the exit
        # plugins as postbuild plugins.
        with open(os.path.join(str(tmpdir), 'simple_inner.json'), 'r+') as inner:
            inner_json = json.load(inner)

            # Re-write all the exit plugins as postbuild plugins
            exit_plugins = inner_json['exit_plugins']
            inner_json['postbuild_plugins'].extend(exit_plugins)
            del inner_json['exit_plugins']

            inner.seek(0)
            json.dump(inner_json, inner)
            inner.truncate()

        bm = BuildManager(str(tmpdir))
        build_request = bm.get_build_request_by_type("simple")
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': "component",
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        # Check the store_metadata_in_osv3's uri parameter was set
        # correctly, even though it was listed as a postbuild plugin.
        assert plugin_value_get(plugins, "postbuild_plugins", "store_metadata_in_osv3", "args", "url") == \
            "http://openshift/"

    def test_render_simple_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type("simple")
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'registry_uri': "http://registry.example.com:5000",
            'openshift_uri': "http://openshift/",
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] is not None
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com:5000/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)
        pull_base_image = get_plugin(plugins, "prebuild_plugins",
                                     "pull_base_image")
        assert pull_base_image is not None
        assert ('args' not in pull_base_image or
                'parent_registry' not in pull_base_image['args'])

        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3", "args", "url") == \
            "http://openshift/"

    def test_render_prod_request_with_repo(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, ProductionBuild)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'yum_repourls': ["http://example.com/my.repo"],
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image",
                                "args", "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")
        assert 'sourceSecret' not in build_json["spec"]["source"]
        assert plugin_value_get(plugins, "prebuild_plugins", "add_yum_repo_by_url",
                                "args", "repourls") == ["http://example.com/my.repo"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Architecture'] is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None

    def test_render_prod_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "root") == "http://root/"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "target") == "koji-target"
        assert plugin_value_get(plugins, "prebuild_plugins", "koji",
                                "args", "hub") == "http://hub/"
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")
        assert 'sourceSecret' not in build_json["spec"]["source"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Architecture'] is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None

    def test_render_prod_without_koji_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITHOUT_KOJI_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        assert isinstance(build_request, ProductionBuild)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["metadata"]["name"] == TEST_BUILD_CONFIG
        assert "triggers" not in build_json["spec"]
        assert build_json["spec"]["source"]["git"]["uri"] == TEST_GIT_URI
        assert build_json["spec"]["source"]["git"]["ref"] == TEST_GIT_REF
        assert build_json["spec"]["output"]["to"]["name"].startswith(
            "registry.example.com/john-foo/component:"
        )

        env_vars = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env_vars:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "distgit_fetch_artefacts",
                                "args", "command") == "make"
        assert plugin_value_get(plugins, "prebuild_plugins", "pull_base_image", "args",
                                "parent_registry") == "registry.example.com"
        assert plugin_value_get(plugins, "exit_plugins", "store_metadata_in_osv3",
                                "args", "url") == "http://openshift/"

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")
        assert 'sourceSecret' not in build_json["spec"]["source"]

        labels = plugin_value_get(plugins, "prebuild_plugins", "add_labels_in_dockerfile",
                                  "args", "labels")

        assert labels is not None
        assert labels['Architecture'] is not None
        assert labels['Authoritative_Registry'] is not None
        assert labels['Build_Host'] is not None
        assert labels['Vendor'] is not None

    def test_render_prod_with_secret_request(self):
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_WITH_SECRET_BUILD_TYPE)
        assert isinstance(build_request, ProductionBuild)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "",
            'pulp_registry': "registry.example.com",
            'nfs_server_path': "server:path",
            'openshift_uri': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'source_secret': 'mysecret',
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert build_json["spec"]["source"]["sourceSecret"]["name"] == "mysecret"

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert get_plugin(plugins, "prebuild_plugins", "koji")
        assert get_plugin(plugins, "postbuild_plugins", "pulp_push")
        assert get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")

    def test_render_with_yum_repourls(self):
        bm = BuildManager(INPUTS_PATH)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'koji_target': "koji-target",
            'kojiroot': "http://root/",
            'kojihub': "http://hub/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
        }
        build_request = bm.get_build_request_by_type("prod")

        # Test validation for yum_repourls parameter
        kwargs['yum_repourls'] = 'should be a list'
        with pytest.raises(OsbsValidationException):
            build_request.set_params(**kwargs)

        # Use a valid yum_repourls parameter and check the result
        kwargs['yum_repourls'] = ['http://example.com/repo1.repo', 'http://example.com/repo2.repo']
        build_request.set_params(**kwargs)
        build_json = build_request.render()
        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)

        repourls = None
        for d in plugins['prebuild_plugins']:
            if d['name'] == 'add_yum_repo_by_url':
                repourls = d['args']['repourls']

        assert repourls is not None
        assert len(repourls) == 2
        assert 'http://example.com/repo1.repo' in repourls
        assert 'http://example.com/repo2.repo' in repourls

        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "bump_release")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "prebuild_plugins", "koji")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "cp_built_image_to_nfs")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "pulp_push")
        with pytest.raises(NoSuchPluginException):
            get_plugin(plugins, "postbuild_plugins", "import_image")

    def test_render_prod_with_pulp_no_auth(self):
        """
        Rendering should fail if pulp is specified but auth config isn't
        """
        bm = BuildManager(INPUTS_PATH)
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': 'fedora/resultingimage',
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'pulp_registry': "foo",
        }
        build_request.set_params(**kwargs)
        with pytest.raises(OsbsValidationException):
            build_request.render()

    def test_render_prod_request_with_trigger(self, tmpdir):
        # Make temporary copies of the JSON files
        for basename in ['prod.json', 'prod_inner.json']:
            shutil.copy(os.path.join(INPUTS_PATH, basename),
                        os.path.join(str(tmpdir), basename))

        # Create a build JSON description with an image change trigger
        with open(os.path.join(str(tmpdir), 'prod.json'), 'r+') as prod_json:
            build_json = json.load(prod_json)

            # Add the image change trigger
            build_json['spec']['triggers'] = [
                {
                    "type": "ImageChange",
                    "imageChange": {
                        "from": {
                            "kind": "ImageStreamTag",
                            "name": "{{BASE_IMAGE_STREAM}}"
                        }
                    }
                }
            ]

            prod_json.seek(0)
            json.dump(build_json, prod_json)
            prod_json.truncate()

        bm = BuildManager(str(tmpdir))
        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        name_label = "fedora/resultingimage"
        push_url = "ssh://{username}git.example.com/git/{component}.git"
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_REF,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': name_label,
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'git_push_url': push_url.format(username='', component=TEST_COMPONENT),
            'git_push_username': 'example',
        }
        build_request.set_params(**kwargs)
        build_json = build_request.render()

        assert "triggers" in build_json["spec"]
        assert build_json["spec"]["triggers"][0]["imageChange"]["from"]["name"] == 'fedora:latest'

        strategy = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in strategy:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        plugins = json.loads(plugins_json)
        assert get_plugin(plugins, "prebuild_plugins", "check_and_set_rebuild")
        assert plugin_value_get(plugins, "prebuild_plugins",
                                "check_and_set_rebuild", "args",
                                "url") == kwargs["openshift_uri"]
        assert get_plugin(plugins, "prebuild_plugins", "bump_release")
        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release", "args",
                                "git_ref") == TEST_GIT_REF
        assert plugin_value_get(plugins, "prebuild_plugins", "bump_release", "args",
                                "push_url") == push_url.format(username='example@',
                                                               component=TEST_COMPONENT)
        assert get_plugin(plugins, "postbuild_plugins", "import_image")
        assert plugin_value_get(plugins,
                                "postbuild_plugins", "import_image", "args",
                                "imagestream") == name_label.replace('/', '-')
        expected_repo = os.path.join(kwargs["registry_uri"], name_label)
        assert plugin_value_get(plugins,
                                "postbuild_plugins", "import_image", "args",
                                "docker_image_repo") == expected_repo
        assert plugin_value_get(plugins,
                                "postbuild_plugins", "import_image", "args",
                                "url") == kwargs["openshift_uri"]

    def test_render_prod_request_new_secrets(self, tmpdir):
        bm = BuildManager(INPUTS_PATH)
        secret_name = 'mysecret'
        kwargs = {
            'git_uri': TEST_GIT_URI,
            'git_ref': TEST_GIT_REF,
            'git_branch': TEST_GIT_BRANCH,
            'user': "john-foo",
            'component': TEST_COMPONENT,
            'base_image': 'fedora:latest',
            'name_label': "fedora/resultingimage",
            'registry_uri': "registry.example.com",
            'openshift_uri': "http://openshift/",
            'sources_command': "make",
            'architecture': "x86_64",
            'vendor': "Foo Vendor",
            'build_host': "our.build.host.example.com",
            'authoritative_registry': "registry.example.com",
            'pulp_registry': 'foo',
            'pulp_secret': secret_name,
        }

        # Default required version (0.5.4), implicitly and explicitly
        for required in (None, parse_version('0.5.4')):
            build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
            if required is not None:
                build_request.set_openshift_required_version(required)

            build_request.set_params(**kwargs)
            build_json = build_request.render()

            # Using the sourceSecret scheme
            assert 'sourceSecret' in build_json['spec']['source']
            assert build_json['spec']['source']\
                ['sourceSecret']['name'] == secret_name

            # Not using the secrets array scheme
            assert 'secrets' not in build_json['spec']['strategy']['customStrategy']

            # We shouldn't have pulp_secret_path set
            env = build_json['spec']['strategy']['customStrategy']['env']
            plugins_json = None
            for d in env:
                if d['name'] == 'DOCK_PLUGINS':
                    plugins_json = d['value']
                    break

            assert plugins_json is not None
            plugins = json.loads(plugins_json)
            assert 'pulp_secret_path' not in plugin_value_get(plugins,
                                                              'postbuild_plugins',
                                                              'pulp_push',
                                                              'args')

        # Set required version to 1.0.6

        build_request = bm.get_build_request_by_type(PROD_BUILD_TYPE)
        build_request.set_openshift_required_version(parse_version('1.0.6'))
        build_json = build_request.render()
        # Not using the sourceSecret scheme
        assert 'sourceSecret' not in build_json['spec']['source']

        # Using the secrets array scheme instead
        assert 'secrets' in build_json['spec']['strategy']['customStrategy']
        secrets = build_json['spec']['strategy']['customStrategy']['secrets']
        pulp_secret = [secret for secret in secrets
                       if secret['secretSource']['name'] == secret_name]
        assert len(pulp_secret) > 0
        assert 'mountPath' in pulp_secret[0]

        # Check that the secret's mountPath matches the plugin's
        # configured path for the secret
        mount_path = pulp_secret[0]['mountPath']
        env = build_json['spec']['strategy']['customStrategy']['env']
        plugins_json = None
        for d in env:
            if d['name'] == 'DOCK_PLUGINS':
                plugins_json = d['value']
                break

        assert plugins_json is not None
        plugins = json.loads(plugins_json)
        assert plugin_value_get(plugins, 'postbuild_plugins', 'pulp_push',
                                'args', 'pulp_secret_path') == mount_path
