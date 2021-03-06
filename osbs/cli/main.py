"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
from __future__ import print_function, absolute_import, unicode_literals
import collections

import json
import logging

from os import uname
import sys
import argparse
from osbs import set_logging
from osbs.api import OSBS
from osbs.conf import Configuration
from osbs.constants import DEFAULT_CONFIGURATION_FILE, DEFAULT_CONFIGURATION_SECTION
from osbs.exceptions import OsbsNetworkException, OsbsException, OsbsAuthException, OsbsResponseException
from osbs.cli.capture import setup_json_capture


logger = logging.getLogger('osbs')


def print_json_nicely(decoded_json):
    print(json.dumps(decoded_json, indent=2))


def cmd_list_builds(args, osbs):
    builds = osbs.list_builds(namespace=args.namespace)
    if args.output == 'json':
        json_output = []
        for build in builds:
            json_output.append(build.json)
        print_json_nicely(json_output)
    elif args.output == 'text':
        format_str = "{name:48} {status:16} {image:64}"
        print(format_str.format(**{"name": "BUILD ID", "status": "STATUS", "image": "IMAGE NAME"}), file=sys.stderr)
        for build in sorted(builds,
                            key=lambda x: x.get_time_created_in_seconds()):
            image = build.get_image_tag()
            if args.USER:
                # image can contain registry - we may have to parse it more intelligently
                registry_and_namespace = image.split("/")[:-1]
                if args.USER not in registry_and_namespace:
                    continue
            b = {
                "name": build.get_build_name(),
                "status": build.status,
                "image": image
            }
            print(format_str.format(**b))


def cmd_get_build(args, osbs):
    build = osbs.get_build(args.BUILD_ID[0], namespace=args.namespace)
    build_json = build.json
    if args.output == 'json':
        print_json_nicely(build_json)
    elif args.output == 'text':
        repositories_dict = build.get_repositories()
        repositories_str = None
        if repositories_dict is not None:
            repositories_template = """\
Primary

{primary}

Unique

{unique}"""
            repositories_context = {
                "primary": "\n".join(repositories_dict["primary"]),
                "unique": "\n".join(repositories_dict["unique"]),
            }
            repositories_str = repositories_template.format(**repositories_context)

        template = """\
BUILD ID: {build_id}
STATUS: {status}
IMAGE: {image}
DATE: {date}

DOCKERFILE

{dockerfile}

BUILD LOGS

{logs}

PACKAGES

{packages}

COMMIT ID

{commit_id}

REPOSITORIES

{repositories}"""
        context = {
            "build_id": build.get_build_name(),
            "status": build.status,
            "image": build.get_image_tag(),
            "date": build.get_time_created(),
            "dockerfile": build.get_dockerfile(),
            "logs": build.get_logs(),
            "packages": build.get_rpm_packages(),
            "repositories": repositories_str,
            "commit_id": build.get_commit_id(),
        }
        print(template.format(**context))


def cmd_cancel_build(args, osbs):
    osbs.cancel_build(args.BUILD_ID[0], namespace=args.namespace)


def cmd_build(args, osbs):
    build = osbs.create_build(
        git_uri=osbs.build_conf.get_git_uri(),
        git_ref=osbs.build_conf.get_git_ref(),
        git_branch=osbs.build_conf.get_git_branch(),
        user=osbs.build_conf.get_user(),
        component=osbs.build_conf.get_component(),
        target=osbs.build_conf.get_koji_target(),
        architecture=osbs.build_conf.get_architecture(),
        yum_repourls=osbs.build_conf.get_yum_repourls(),
        namespace=osbs.build_conf.get_namespace(),
    )
    build_id = build.get_build_name()
    # we need to wait for kubelet to schedule the build, otherwise it's 500
    namespace = osbs.build_conf.get_namespace()
    build = osbs.wait_for_build_to_get_scheduled(build_id, namespace=namespace)
    if not args.no_logs:
        build_logs = osbs.get_build_logs(build_id, follow=True, namespace=namespace)
        if not isinstance(build_logs, collections.Iterable):
            logger.error("'%s' is not iterable; can't display logs", build_logs)
            return
        print("Build submitted (%s), watching logs (feel free to interrupt)" % build_id)
        try:
            for line in build_logs:
                print(line)
        except Exception as ex:
            logger.error("Error during fetching logs for build %s: %s", build_id, repr(ex))
    else:
        if args.output == 'json':
            print_json_nicely(build.json)
        elif args.output == 'text':
            print(build_id)


def cmd_build_logs(args, osbs):
    build_id = args.BUILD_ID[0]
    follow = args.follow

    if follow and args.from_docker_build:
        print("Can't use --follow and --from-docker-build. "
              "Logs from docker build are part of metadata of a already built image.")
        return

    if args.from_docker_build:
        logs = osbs.get_docker_build_logs(build_id, namespace=args.namespace)
    else:
        logs = osbs.get_build_logs(build_id, follow=follow,
                                   wait_if_missing=args.wait_if_missing,
                                   namespace=args.namespace)
        if follow:
            for line in logs:
                print(line)
            return
    print(logs, end="")


def cmd_watch_build(args, osbs):
    build_response = osbs.wait_for_build_to_finish(args.BUILD_ID[0], namespace=args.namespace)
    if args.output == 'text':
        pass
    elif args.output == 'json':
        print_json_nicely(build_response.json)


def cmd_import_image(args, osbs):
    osbs.import_image(args.NAME[0])


def cmd_get_token(args, osbs):  # pylint: disable=W0613
    token = osbs.get_token()
    print(token)


def cmd_get_user(args, osbs):
    args_username = args.USERNAME
    if args_username is None:
        user_json = osbs.get_user()
    else:
        args_username = args_username[0]
        user_json = osbs.get_user(args_username)
    if args.output == 'json':
        print_json_nicely(user_json)
    elif args.output == 'text':
        name = ""
        full_name = ""
        try:
            name = user_json["metadata"]["name"]
        except KeyError:
            logger.error("\"name\" is not in response")
        try:
            full_name = user_json["fullName"]
        except KeyError:
            logger.error("\"full name\" is not in response")
        print("Name: \"%s\"\nFull Name: \"%s\"" % (name, full_name))


def cmd_get_build_image_id(args, osbs):
    pod = osbs.get_pod_for_build(args.BUILD_ID[0], namespace=args.namespace)
    if args.output == 'json':
        json_output = pod.get_container_image_ids()
        print_json_nicely(json_output)
    elif args.output == 'text':
        format_str = "{tag:18} {image:64}"
        print(format_str.format(tag='TAG', image='IMAGE ID'), file=sys.stderr)
        image_ids = pod.get_container_image_ids()
        for name, image_id in image_ids.items():
            print(format_str.format(tag=name, image=image_id))


def str_on_2_unicode_on_3(s):
    """
    argparse is way too awesome when doing repr() on choices when printing usage

    :param s: str or unicode
    :return: str on 2, unicode on 3
    """

    if sys.version_info[0] <= 2:
        return str(s)
    else:  # 3+
        if not isinstance(s, str):
            return str(s, encoding="utf-8")
        return s


def cli():
    parser = argparse.ArgumentParser(
        description="OpenShift Build Service client"
    )
    exclusive_group = parser.add_mutually_exclusive_group()
    exclusive_group.add_argument("--verbose", action="store_true", default=None)
    exclusive_group.add_argument("-q", "--quiet", action="store_true")

    subparsers = parser.add_subparsers(help='commands')

    list_builds_parser = subparsers.add_parser(str_on_2_unicode_on_3('list-builds'), help='list builds in OSBS',
                                               description="list all builds in specified namespace "
                                               "(to list all builds in all namespaces, use --namespace=\"\")")
    list_builds_parser.add_argument("USER", help="list builds only for specified username",
                                    nargs="?")
    list_builds_parser.set_defaults(func=cmd_list_builds)

    watch_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('watch-build'), help='wait till build finishes')
    watch_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    watch_build_parser.set_defaults(func=cmd_watch_build)

    get_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-build'), help='get info about build')
    get_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    get_build_parser.set_defaults(func=cmd_get_build)

    cancel_build_parser = subparsers.add_parser(str_on_2_unicode_on_3('cancel-build'), help='cancel build specified by ID')
    cancel_build_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    cancel_build_parser.set_defaults(func=cmd_cancel_build)

    import_image_parser = subparsers.add_parser(str_on_2_unicode_on_3('import-image'), help='import tags for ImageStream')
    import_image_parser.add_argument("NAME", help="ImageStream name", nargs=1)
    import_image_parser.set_defaults(func=cmd_import_image)

    get_token_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-token'), help='get authentication token')
    get_token_parser.set_defaults(func=cmd_get_token)

    get_user_parser = subparsers.add_parser(str_on_2_unicode_on_3('get-user'), help='get info about user')
    get_user_parser.add_argument("USERNAME", nargs="?", default=None)
    get_user_parser.set_defaults(func=cmd_get_user)

    build_logs_parser = subparsers.add_parser(str_on_2_unicode_on_3('build-logs'), help='get or follow build logs')
    build_logs_parser.add_argument("BUILD_ID", help="build ID", nargs=1)
    build_logs_parser.add_argument("-f", "--follow", help="follow logs as they come", action="store_true",
                                   default=False)
    build_logs_parser.add_argument("--wait-if-missing", help="if build is not created yet, wait", action="store_true",
                                   default=False)
    build_logs_parser.add_argument("--from-docker-build", help="return logs from `docker build` instead",
                                   action="store_true", default=False)
    build_logs_parser.set_defaults(func=cmd_build_logs)

    build_parser = subparsers.add_parser(str_on_2_unicode_on_3('build'), help='build an image in OSBS')
    build_parser.add_argument("--build-type", "-T", action="store", metavar="BUILD_TYPE",
                              help="build type (prod, simple)")
    build_parser.add_argument("--build-json-dir", action="store", metavar="PATH",
                              help="directory with build jsons")
    build_parser.add_argument("-g", "--git-url", action='store', metavar="URL",
                              required=True, help="URL to git repo (fetch)")
    build_parser.add_argument("--git-push-url", action='store', metavar="URL",
                              required=False, help="URL to git repo (push)")
    build_parser.add_argument("--git-push-username", action='store',
                              required=False, help="username for git push")
    build_parser.add_argument("--git-commit", action='store', default="master",
                              help="checkout this commit")
    build_parser.add_argument("-b", "--git-branch", action='store', required=True,
                              help="name of git branch (for incrementing Release)")
    build_parser.add_argument("-t", "--target", action='store',
                              help="koji target name")
    build_parser.add_argument("-a", "--arch", action='store', default=uname()[4],
                              help="build architecture")
    build_parser.add_argument("-u", "--user", action='store', required=True,
                              help="prefix for docker image repository")
    build_parser.add_argument("-c", "--component", action='store', required=True,
                              help="name of component")
    build_parser.add_argument("--no-logs", action='store_true', required=False, default=False,
                              help="don't print logs after submitting build")
    build_parser.add_argument("--add-yum-repo", action='append', metavar="URL",
                              dest="yum_repourls", help="URL of yum repo file")
    build_parser.add_argument("--source-secret", action='store', required=False,
                              help="resource name of source secret")
    build_parser.add_argument("--cpu-limit", action='store', required=False,
                              help="CPU limit (KCU)")
    build_parser.add_argument("--memory-limit", action='store', required=False,
                              help="memory limit")
    build_parser.add_argument("--storage-limit", action='store', required=False,
                              help="storage limit")
    build_parser.set_defaults(func=cmd_build)

    get_build_image_id = subparsers.add_parser(str_on_2_unicode_on_3('get-build-image-id'),
                                                help='get build container image ID',
                                                description='get build container images for a build in a namespace')
    get_build_image_id.add_argument("BUILD_ID", help="build ID", nargs=1)
    get_build_image_id.set_defaults(func=cmd_get_build_image_id)

    parser.add_argument("--openshift-uri", action='store', metavar="URL",
                        help="openshift URL to remote API")
    parser.add_argument("--registry-uri", action='store', metavar="URL",
                        help="registry where images should be pushed")
    parser.add_argument("--config", action='store', metavar="PATH",
                        help="path to configuration file", default=DEFAULT_CONFIGURATION_FILE)
    parser.add_argument("--instance", "-i", action='store', metavar="SECTION_NAME",
                        help="section within config for requested instance", default=DEFAULT_CONFIGURATION_SECTION)
    parser.add_argument("--username", action='store',
                        help="name of user to use for Basic Authentication in OSBS")
    parser.add_argument("--password", action='store',
                        help="password to use for Basic Authentication in OSBS")
    parser.add_argument("--use-kerberos", action='store_true', default=None,
                        help="use kerberos for authentication")
    parser.add_argument("--client-cert", action='store',
                        help="path to client certificate in PEM format to use for authentication")
    parser.add_argument("--client-key", action='store',
                        help="path to key file for the certificate provided with --client-cert")
    parser.add_argument("--kerberos-keytab", action='store',
                        help="path to kerberos keytab to obtain credentials from")
    parser.add_argument("--kerberos-principal", action='store',
                        help="kerberos principal for the provided keytab")
    parser.add_argument("--kerberos-ccache", action='store',
                        help="path to credential cache to use instead of the default one")
    parser.add_argument("--verify-ssl", action='store_true', default=None,
                        help="verify CA on secure connections")
    parser.add_argument("--with-auth", action="store_true", dest="use_auth", default=None,
                        help="get and supply oauth token with every request")
    parser.add_argument("--without-auth", action="store_false", dest="use_auth", default=None,
                        help="don't supply oauth tokens to requests")
    parser.add_argument("--output", choices=["json", "text"], default="text",
                        help="pick output type (default=text)")
    parser.add_argument("--namespace", help="name of namespace to query against "
                                            "(you may require blank namespace with --namespace=\"\")",
                        metavar="NAMESPACE", action="store", default="default")
    parser.add_argument("--capture-dir", metavar="DIR", action="store",
                        help="capture JSON responses and save them in DIR")
    args = parser.parse_args()
    return parser, args


def main():
    parser, args = cli()
    try:
        os_conf = Configuration(conf_file=args.config,
                                conf_section=args.instance,
                                cli_args=args)
        build_conf = Configuration(conf_file=args.config,
                                   conf_section=args.instance,
                                   cli_args=args)
    except OsbsException as ex:
        logger.error("Configuration error: %s", ex.message)
        return -1

    is_verbose = os_conf.get_verbosity()

    if is_verbose:
        set_logging(level=logging.DEBUG)
        logger.debug("Logging level set to debug")
    elif args.quiet:
        set_logging(level=logging.WARNING)
    else:
        set_logging(level=logging.INFO)

    osbs = OSBS(os_conf, build_conf)

    if args.capture_dir is not None:
        setup_json_capture(osbs, os_conf, args.capture_dir)

    try:
        args.func(args, osbs)
    except AttributeError as ex:
        if hasattr(args, 'func'):
            raise
        else:
            parser.print_help()
    except KeyboardInterrupt:
        print("Quitting on user request.")
        return -1
    except OsbsNetworkException as ex:
        if is_verbose:
            raise
        else:
            logger.error("Network error at %s (%d): %s",
                         ex.url, ex.status_code, ex.message)
            return -1
    except OsbsAuthException as ex:
        if is_verbose:
            raise
        else:
            logger.error("Authentication failure: %s",
                         ex.message)
            return -1
    except OsbsResponseException as ex:
        if is_verbose:
            raise
        else:
            if isinstance(ex.json, dict) and 'message' in ex.json:
                msg = ex.json['message']
            else:
                msg = ex.message
            logger.error("Server returned error %s: %s", ex.status_code, msg)
            return -1
    except Exception as ex:  # pylint: disable=broad-except
        if is_verbose:
            raise
        else:
            logger.error("Exception caught: %s", repr(ex))
            return -1

if __name__ == '__main__':
    sys.exit(main())
