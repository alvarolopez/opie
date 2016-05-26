# Copyright 2016 Spanish National Research Council - CSIC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import datetime
import uuid

from opie.api.openstack.compute import preemptible_instances as preempt_api
from opie.tests import base

from nova.api.openstack.compute import extension_info
from nova.api.openstack.compute import servers
from nova.compute import api as compute_api
from nova.compute import flavors
from nova import db
from nova.network import manager as net_manager
from nova import test as nova_test
from nova.tests.unit.api.openstack import fakes as os_api_fakes
from nova.tests.unit import fake_instance
from nova.tests.unit.image import fake as os_image_fake
from oslo_config import cfg
from oslo_serialization import jsonutils
import webob.exc

CONF = cfg.CONF
FAKE_UUID = os_api_fakes.FAKE_UUID


def fake_gen_uuid():
    return FAKE_UUID


def return_security_group(context, instance_id, security_group_id):
    pass


class TestOpieAPI(base.TestCase):
    def setUp(self):
        super(TestOpieAPI, self).setUp()
        self.controller = preempt_api.SpotController()

        self.req = None

    def test_index(self):
        self.assertRaises(webob.exc.HTTPNotImplemented,
                          self.controller.index,
                          self.req)


class ServersControllerCreateTest(nova_test.TestCase):
    def setUp(self):
        """Shared implementation for tests below that create instance."""
        super(ServersControllerCreateTest, self).setUp()

        self.instance_cache_num = 0
        self.instance_cache_by_id = {}
        self.instance_cache_by_uuid = {}

        ext_info = extension_info.LoadedExtensionInfo()
        self.controller = servers.ServersController(extension_info=ext_info)
        CONF.set_override('extensions_blacklist', preempt_api.ALIAS,
                          'osapi_v21')
        self.no_preemptible_controller = servers.ServersController(
            extension_info=ext_info)

        def instance_create(context, inst):
            inst_type = flavors.get_flavor_by_flavor_id(3)
            image_uuid = '76fa36fc-c930-4bf3-8c8a-ea2a2420deb6'
            def_image_ref = 'http://localhost/images/%s' % image_uuid
            self.instance_cache_num += 1
            instance = fake_instance.fake_db_instance(**{
                'id': self.instance_cache_num,
                'display_name': inst['display_name'] or 'test',
                'uuid': FAKE_UUID,
                'instance_type': inst_type,
                'access_ip_v4': '1.2.3.4',
                'access_ip_v6': 'fead::1234',
                'image_ref': inst.get('image_ref', def_image_ref),
                'user_id': 'fake',
                'project_id': 'fake',
                'reservation_id': inst['reservation_id'],
                "created_at": datetime.datetime(2010, 10, 10, 12, 0, 0),
                "updated_at": datetime.datetime(2010, 11, 11, 11, 0, 0),
                "progress": 0,
                "fixed_ips": [],
                "task_state": "",
                "vm_state": "",
                "root_device_name": inst.get('root_device_name', 'vda'),
            })

            self.instance_cache_by_id[instance['id']] = instance
            self.instance_cache_by_uuid[instance['uuid']] = instance
            return instance

        def instance_get(context, instance_id):
            """Stub for compute/api create() pulling in instance after
            scheduling
            """
            return self.instance_cache_by_id[instance_id]

        def instance_update(context, uuid, values):
            instance = self.instance_cache_by_uuid[uuid]
            instance.update(values)
            return instance

        def server_update(context, instance_uuid, params):
            inst = self.instance_cache_by_uuid[instance_uuid]
            inst.update(params)
            return (inst, inst)

        def fake_method(*args, **kwargs):
            pass

        def project_get_networks(context, user_id):
            return dict(id='1', host='localhost')

        os_api_fakes.stub_out_rate_limiting(self.stubs)
        os_api_fakes.stub_out_key_pair_funcs(self.stubs)
        os_image_fake.stub_out_image_service(self.stubs)
        os_api_fakes.stub_out_nw_api(self.stubs)
        self.stubs.Set(uuid, 'uuid4', fake_gen_uuid)
        self.stubs.Set(db, 'instance_add_security_group',
                       return_security_group)
        self.stubs.Set(db, 'project_get_networks',
                       project_get_networks)
        self.stubs.Set(db, 'instance_create', instance_create)
        self.stubs.Set(db, 'instance_system_metadata_update',
                       fake_method)
        self.stubs.Set(db, 'instance_get', instance_get)
        self.stubs.Set(db, 'instance_update', instance_update)
        self.stubs.Set(db, 'instance_update_and_get_original',
                       server_update)
        self.stubs.Set(net_manager.VlanManager, 'allocate_fixed_ip',
                       fake_method)

    def _test_create_extra(self, params, no_image=False,
                           override_controller=None):
        image_uuid = 'c905cedb-7281-47e4-8a62-f26bc5fc4c77'
        server = dict(name='server_test', imageRef=image_uuid, flavorRef=2)
        if no_image:
            server.pop('imageRef', None)
        server.update(params)
        body = dict(server=server)
        req = os_api_fakes.HTTPRequestV21.blank('/servers')
        req.method = 'POST'
        req.body = jsonutils.dump_as_bytes(body)
        req.headers["content-type"] = "application/json"
        if override_controller:
            server = override_controller.create(req, body=body).obj['server']
        else:
            server = self.controller.create(req, body=body).obj['server']
        return server

    def test_create_preemtible_instance(self):
        params = {preempt_api.ATTRIBUTE_NAME: True}
        old_create = compute_api.API.create

        def create(*args, **kwargs):
            self.assertIn('preemptible', kwargs)
            del kwargs['preemptible']
            return old_create(*args, **kwargs)

        self.stubs.Set(compute_api.API, 'create', create)
        server = self._test_create_extra(params)
        self.assertEqual(FAKE_UUID, server['id'])
