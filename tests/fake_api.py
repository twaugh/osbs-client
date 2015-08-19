"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import absolute_import, unicode_literals, print_function

import os
import re
import pytest
import inspect
import logging
from osbs.core import Openshift
from osbs.http import HttpResponse
from osbs.conf import Configuration
from osbs.api import OSBS
from tests.constants import (TEST_BUILD, TEST_COMPONENT, TEST_GIT_REF,
                             TEST_GIT_BRANCH, TEST_BUILD_CONFIG)
from tempfile import NamedTemporaryFile

try:
    # py2
    import urlparse
except ImportError:
    # py3
    import urllib.parse as urlparse


logger = logging.getLogger("osbs.tests")
API_PREFIX = "/osapi/{v}/".format(v=Configuration.get_openshift_api_version())


class StreamingResponse(object):
    def __init__(self, status_code=200, content=b'', headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def iter_lines(self):
        yield self.content.decode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class Connection(object):
    def __init__(self, version="0.5.2"):
        self.version = version
        self.response_mapping = ResponseMapping(version,
                                                lookup=self.get_definition_for)

        # mapping of urls or tuples of urls to responses; use get_definition_for
        #  to get values from this dict
        self.DEFINITION = {
            API_PREFIX + "namespaces/default/builds/": {
                "get": {
                    "file": "builds_list.json",
                },
                "post": {
                    "file": "build_test-build-123.json",
                },
            },

            # Some 'builds' requests are with a trailing slash, some without:
            (API_PREFIX + "namespaces/default/builds/%s" % TEST_BUILD,
             API_PREFIX + "namespaces/default/builds/%s/" % TEST_BUILD): {
                 "get": {
                     "file": "build_test-build-123.json",
                 },
                 "put": {
                     "file": "build_test-build-123.json",
                 }
             },

            (API_PREFIX + "namespaces/default/builds/%s/log/" % TEST_BUILD,
             API_PREFIX + "namespaces/default/builds/%s/log/?follow=0" % TEST_BUILD,
             API_PREFIX + "namespaces/default/builds/%s/log/?follow=1" % TEST_BUILD): {
                 "get": {
                     "file": "build_test-build-123_logs.txt",
                 },
             },

            ("/oauth/authorize",
             "/oauth/authorize?client_id=openshift-challenging-client&response_type=token",
             "/oauth/authorize?response_type=token&client_id=openshift-challenging-client"): {
                 "get": {
                     "file": "authorize.txt",
                     "custom_callback": self.process_authorize,
                 }
             },

            API_PREFIX + "users/~/": {
                "get": {
                    "file": "get_user.json",
                }
            },

            API_PREFIX + "watch/namespaces/default/builds/%s/" % TEST_BUILD: {
                "get": {
                    "file": "watch_build_test-build-123.json",
                }
            },

            API_PREFIX + "namespaces/default/buildconfigs/": {
                "post": {
                    "file": "created_build_config_test-build-config-123.json",
                }
            },

            API_PREFIX + "namespaces/default/buildconfigs/%s/instantiate" % TEST_BUILD_CONFIG: {
                "post": {
                    "file": "instantiated_test-build-config-123.json",
                }
            },

            # use both version with ending slash and without it
            (API_PREFIX + "namespaces/default/buildconfigs/%s" % TEST_BUILD_CONFIG,
             API_PREFIX + "namespaces/default/buildconfigs/%s/" % TEST_BUILD_CONFIG): {
                 "get": {
                     "custom_callback": self.buildconfig_not_found,
                     "file": "not_found_build-config-component-master.json",
                 }
             },

            API_PREFIX + "namespaces/default/builds/?labelSelector=buildconfig%%3D%s" %
            TEST_BUILD_CONFIG: {
                "get": {
                    "file": "builds_list_fedora23-something_no_running.json"
                }
            },
        }


    @staticmethod
    def process_authorize(key, content):
        match = re.findall(b"[Ll]ocation: (.+)", content)
        headers = {
            "location": match[0],
        }
        logger.debug("headers: %s", headers)
        return {
            "headers": headers
        }

    @staticmethod
    def buildconfig_not_found(key, content):
        return {
            "status_code": 404,
        }

    def get_definition_for(self, key):
        """
        Returns key and value associated with given key in DEFINITION dict.

        This means that either key is an actual dict key in DEFINITION or it is member
        of a tuple that serves as a dict key in DEFINITION.
        """
        try:
            # Try a direct look-up
            return key, self.DEFINITION[key]
        except KeyError:
            # Try all the tuples
            for k, v in self.DEFINITION.items():
                if isinstance(k, tuple) and key in k:
                    return k, v

            raise ValueError("Can't find '%s' in url mapping definition" % key)

    @staticmethod
    def response(status_code=200, content=b'', headers=None):
        return HttpResponse(status_code, headers or {}, content.decode("utf-8"))

    def _request(self, url, method, stream=None, *args, **kwargs):
        parsed_url = urlparse.urlparse(url)
        # fragment = parsed_url.fragment
        # parsed_fragment = urlparse.parse_qs(fragment)
        url_path = parsed_url.path
        if parsed_url.query:
            url_path += '?' + parsed_url.query
        logger.info("URL path is '%s'", url_path)
        kwargs = self.response_mapping.response_mapping(url_path, method)
        if stream:
            return StreamingResponse(**kwargs)
        else:
            return self.response(**kwargs)

    def get(self, url, *args, **kwargs):
        return self._request(url, "get", *args, **kwargs)

    def post(self, url, *args, **kwargs):
        return self._request(url, "post", *args, **kwargs)

    def put(self, url, *args, **kwargs):
        return self._request(url, "put", *args, **kwargs)


@pytest.fixture(params=["0.5.2", "0.5.4"])
def openshift(request):
    os_inst = Openshift(API_PREFIX, "/oauth/authorize")
    os_inst._con = Connection(request.param)
    return os_inst


@pytest.fixture
def osbs(openshift):
    with NamedTemporaryFile(mode="wt") as fp:
        fp.write("""
[general]
build_json_dir = {build_json_dir}
[default]
openshift_uri = https://0.0.0.0/
registry_uri = registry.example.com
sources_command = fedpkg sources
vendor = Example, Inc.
build_host = localhost
authoritative_registry = registry.example.com
koji_root = http://koji.example.com/kojiroot
koji_hub = http://koji.example.com/kojihub
build_type = simple
use_auth = false
""".format (build_json_dir="inputs"))
        fp.flush()
        dummy_config = Configuration(fp.name)
        osbs = OSBS(dummy_config, dummy_config)

    osbs.os = openshift
    return osbs


class ResponseMapping(object):
    def __init__(self, version, lookup):
        self.version = version
        self.lookup = lookup

    def get_response_content(self, file_name):
        this_file = inspect.getfile(ResponseMapping)
        this_dir = os.path.dirname(this_file)
        json_path = os.path.join(this_dir, "mock_jsons", self.version, file_name)
        logger.debug("File: %s", json_path)
        with open(json_path, "r") as fd:
            return fd.read().encode("utf-8")

    def response_mapping(self, url_path, method):
        key, value_to_use = self.lookup(url_path)
        file_name = value_to_use[method]["file"]
        logger.debug("API response content: %s", file_name)
        custom_callback = value_to_use[method].get("custom_callback", None)
        content = self.get_response_content(file_name)
        if custom_callback:
            logger.debug("Custom API callback: %s", custom_callback)
            return custom_callback(key, content)
        else:
            return {"content": content}

