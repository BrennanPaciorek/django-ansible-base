# Copyright 2024 Red Hat, Inc.
# Copyright (c) 2015, Rob Golding. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of Rob Golding, nor the names of its contributors may
#       be used to endorse or promote products derived from this software
#       without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# NOTE(cutwater): Disabling linters to keep diff as little as possible.
# fmt: off
# isort: skip_file
# flake8: noqa
import logging
from io import StringIO

from pyrad.packet import AccessRequest, AccessAccept, AccessReject
from pyrad.client import Client, Timeout
from pyrad.dictionary import Dictionary

from django.conf import settings as global_settings
#Handle custom user models
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

logger = logging.getLogger('ansible_base.authentication.authenticator_plugins._radiusauth')

User = get_user_model()

DICTIONARY = u"""
ATTRIBUTE   User-Name       1   string
ATTRIBUTE   User-Password       2   string
ATTRIBUTE   CHAP-Password       3   octets
ATTRIBUTE   NAS-IP-Address      4   ipaddr
ATTRIBUTE   NAS-Port        5   integer
ATTRIBUTE   Service-Type        6   integer
ATTRIBUTE   Framed-Protocol     7   integer
ATTRIBUTE   Framed-IP-Address   8   ipaddr
ATTRIBUTE   Framed-IP-Netmask   9   ipaddr
ATTRIBUTE   Framed-Routing      10  integer
ATTRIBUTE   Filter-Id       11  string
ATTRIBUTE   Framed-MTU      12  integer
ATTRIBUTE   Framed-Compression  13  integer
ATTRIBUTE   Login-IP-Host       14  ipaddr
ATTRIBUTE   Login-Service       15  integer
ATTRIBUTE   Login-TCP-Port      16  integer
ATTRIBUTE   Reply-Message       18  string
ATTRIBUTE   Callback-Number     19  string
ATTRIBUTE   Callback-Id     20  string
ATTRIBUTE   Framed-Route        22  string
ATTRIBUTE   Framed-IPX-Network  23  ipaddr
ATTRIBUTE   State           24  octets
ATTRIBUTE   Class           25  octets
ATTRIBUTE   Vendor-Specific     26  octets
ATTRIBUTE   Session-Timeout     27  integer
ATTRIBUTE   Idle-Timeout        28  integer
ATTRIBUTE   Termination-Action  29  integer
ATTRIBUTE   Called-Station-Id   30  string
ATTRIBUTE   Calling-Station-Id  31  string
ATTRIBUTE   NAS-Identifier      32  string
ATTRIBUTE   Proxy-State     33  octets
ATTRIBUTE   Login-LAT-Service   34  string
ATTRIBUTE   Login-LAT-Node      35  string
ATTRIBUTE   Login-LAT-Group     36  octets
ATTRIBUTE   Framed-AppleTalk-Link   37  integer
ATTRIBUTE   Framed-AppleTalk-Network 38 integer
ATTRIBUTE   Framed-AppleTalk-Zone   39  string
"""

REALM_SEPARATOR = '@'


class RADIUSBackend(object):
    """
    Standard RADIUS authentication backend for Django. Uses the server details
    specified in settings.py (RADIUS_SERVER, RADIUS_PORT and RADIUS_SECRET).
    """
    supports_anonymous_user = False
    supports_object_permissions = False

    def __init__(self, settings=None):
        if settings is None:
            settings = global_settings
        self.settings = settings

    def _get_dictionary(self):
        """
        Get the pyrad Dictionary object which will contain our RADIUS user's
        attributes. Fakes a file-like object using StringIO.
        """
        return Dictionary(StringIO(DICTIONARY))

    def _get_auth_packet(self, username, password, client):
        """
        Get the pyrad authentication packet for the username/password and the
        given pyrad client.
        """
        pkt = client.CreateAuthPacket(code=AccessRequest,
                                      User_Name=username)
        pkt["User-Password"] = pkt.PwCrypt(password)
        pkt["NAS-Identifier"] = 'django-radius'
        for key, val in list(getattr(self.settings, 'RADIUS_ATTRIBUTES', {}).items()):
            pkt[key] = val
        return pkt

    def _get_client(self, server):
        """
        Get the pyrad client for a given server. RADIUS server is described by
        a 3-tuple: (<hostname>, <port>, <secret>).
        """
        return Client(
            server=server[0],
            authport=server[1],
            secret=server[2],
            dict=self._get_dictionary(),
        )

    def _get_server_from_settings(self):
        """
        Get the RADIUS server details from the settings file.
        """
        return (
            self.settings.RADIUS_SERVER,
            int(self.settings.RADIUS_PORT),
            self.settings.RADIUS_SECRET.encode('utf-8'),
        )

    def _perform_radius_auth(self, client, packet):
        """
        Perform the actual radius authentication by passing the given packet
        to the server which `client` is bound to.
        Returns a tuple (list of groups, is_staff, is_superuser) or None depending on whether the user is authenticated
        successfully.
        """
        try:
            reply = client.SendPacket(packet)
        except Timeout as e:
            logger.error("RADIUS timeout occurred contacting %s:%s" % (
                client.server, client.authport))
            return None
        except Exception as e:
            logger.error("RADIUS error: %s" % e)
            return None

        if reply.code == AccessReject:
            logger.warning("RADIUS access rejected for user '%s'" % (
                packet['User-Name']))
            return None
        elif reply.code != AccessAccept:
            logger.error("RADIUS access error for user '%s' (code %s)" % (
                packet['User-Name'], reply.code))
            return None

        logger.info("RADIUS access granted for user '%s'" % (
            packet['User-Name']))

        if "Class" not in reply.keys():
            return [], False, False

        groups = []
        is_staff = False
        is_superuser = False

        app_class_prefix = getattr(self.settings, 'RADIUS_CLASS_APP_PREFIX', '')
        group_class_prefix = app_class_prefix + "group="
        role_class_prefix = app_class_prefix + "role="

        for cl in reply['Class']:
            cl = cl.decode("utf-8")
            if cl.lower().find(group_class_prefix) == 0:
                groups.append(cl[len(group_class_prefix):])
            elif cl.lower().find(role_class_prefix) == 0:
                role = cl[len(role_class_prefix):]
                if role == "staff":
                    is_staff = True
                elif role == "superuser":
                    is_superuser = True
                else:
                    logger.warning("RADIUS Attribute Class contains unknown role '%s'. Only roles 'staff' and 'superuser' are allowed" % cl)
        return groups, is_staff, is_superuser

    def _radius_auth(self, server, username, password):
        """
        Authenticate the given username/password against the RADIUS server
        described by `server`.
        """
        client = self._get_client(server)
        packet = self._get_auth_packet(username, password, client)
        return self._perform_radius_auth(client, packet)

    def get_django_user(self, username, password=None, groups=None, is_staff=False, is_superuser=False):
        """
        Get the Django user with the given username, or create one if it
        doesn't already exist. If `password` is given, then set the user's
        password to that (regardless of whether the user was created or not).
        """
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            user = User(username=username)

        # if RADIUS_REMOTE_ROLES is not set, configure it to the default value
        # of versions <= 1.4.0
        if not hasattr(self.settings, "RADIUS_REMOTE_ROLES"):
            self.settings.RADIUS_REMOTE_ROLES = True

        if self.settings.RADIUS_REMOTE_ROLES:
            user.is_staff = is_staff
            user.is_superuser = is_superuser
        if password is not None:
            user.set_password(password)

        user.save()
        user.groups.set(groups)
        return user

    def get_user_groups(self, group_names):
        groups = Group.objects.filter(name__in=group_names)
        if len(groups) != len(group_names):
            local_group_names = [g.name for g in groups]
            logger.warning("RADIUS reply contains %d user groups (%s), but only %d (%s) found" % (
                len(group_names), ", ".join(group_names), len(groups), ", ".join(local_group_names)))
        return groups

    def authenticate(self, request, username=None, password=None):
        """
        Check credentials against RADIUS server and return a User object or
        None.
        """

        server = self._get_server_from_settings()
        result = self._radius_auth(server, username, password)

        if result:
            group_names, is_staff, is_superuser = result
            groups = self.get_user_groups(group_names)
            return self.get_django_user(username, password, groups, is_staff, is_superuser)

        return None

    def get_user(self, user_id):
        """
        Get the user with the ID of `user_id`. Authentication backends must
        implement this method.
        """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


class RADIUSRealmBackend(RADIUSBackend):
    """
    Advanced realm-based RADIUS backend. Authenticates users with a username,
    password and a realm (simply a unique string). The server to authenticate
    with is defined by the result of calling get_server(realm) on an instance
    of this class.

    By default, this class uses the RADIUS server specified in the settings
    file, regardless of the realm. Subclasses should override the `get_server`
    method to provide their own logic. The method should return a 3-tuple:
    (<hostname>, <port>, <secret>).
    """
    def get_server(self, realm):
        """
        Get the details of the RADIUS server to authenticate users of the given
        realm.

        Returns a 3-tuple (<hostname>, <port>, <secret>). Base implementation
        always returns the RADIUS server specified in the main settings file,
        and should be overridden.
        """
        return self._get_server_from_settings()

    def construct_full_username(self, username, realm):
        """
        Construct a unique username for a user, given their normal username and
        realm. This is to avoid conflicts in the Django auth app, as usernames
        must be unique.

        By default, returns a string in the format <username>@<realm>.
        """
        return '%s@%s' % (username, realm)

    def authenticate(self, request, username=None, password=None, realm=None):
        """
        Check credentials against the RADIUS server identified by `realm` and
        return a User object or None. If no argument is supplied, Django will
        skip this backend and try the next one (as a TypeError will be raised
        and caught).
        """

        server = self.get_server(realm)

        if not server:
            return None

        result = self._radius_auth(server, username, password)

        if result:
            full_username = self.construct_full_username(username, realm)
            group_names, is_staff, is_superuser = result
            groups = self.get_user_groups(group_names)
            return self.get_django_user(full_username, password, groups, is_staff, is_superuser)

        return None
