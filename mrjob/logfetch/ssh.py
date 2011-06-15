# Copyright 2009-2011 Yelp and Contributors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os
import re
import subprocess

from mrjob.logfetch import LogFetcher, LogFetchException


log = logging.getLogger('mrjob.fetch_s3')


# regex for matching task-attempts log URIs
TASK_ATTEMPTS_LOG_URI_RE = re.compile(r'^.*/hadoop/userlogs/attempt_(?P<timestamp>\d+)_(?P<step_num>\d+)_(?P<node_type>m|r)_(?P<node_num>\d+)_(?P<attempt_num>\d+)/(?P<stream>stderr|syslog)$')

# regex for matching step log URIs
STEP_LOG_URI_RE = re.compile(r'^.*/hadoop/steps/(?P<step_num>\d+)/syslog$')

# regex for matching job log URIs
JOB_LOG_URI_RE = re.compile(r'^.*?/hadoop/history/.+?_(?P<mystery_string_1>\d+)_job_(?P<timestamp>\d+)_(?P<step_num>\d+)_hadoop_streamjob(?P<mystery_string_2>\d+).jar$')


class SSHLogFetcher(LogFetcher):

    def __init__(self, emr_conn, jobflow_id, 
                 ec2_key_pair_file='/nail/etc/EMR.pem.dev',
                 local_temp_dir='/tmp'):
        super(SSHLogFetcher, self).__init__(local_temp_dir=local_temp_dir)
        self.emr_conn = emr_conn
        self.jobflow_id = jobflow_id
        self.ec2_key_pair_file = ec2_key_pair_file
        self.root_path = '/mnt/var/log/hadoop/'
        self._uri_of_downloaded_log_file = None
        self.address = None

    def task_attempts_log_uri_re(self):
        return TASK_ATTEMPTS_LOG_URI_RE

    def step_log_uri_re(self):
        return STEP_LOG_URI_RE

    def job_log_uri_re(self):
        return JOB_LOG_URI_RE

    def task_attempts_log_path(self):
        return 'userlogs'

    def step_log_path(self):
        return 'steps'

    def job_log_path(self):
        return 'history'

    def _address_of_master(self):
        # cache address of master to avoid redundant calls to describe_jobflow
        if self.address:
            return self.address

        jobflow = self.emr_conn.describe_jobflow(self.jobflow_id)
        if jobflow.state not in ('WAITING', 'RUNNING'):
            raise LogFetchException('Cannot ssh to master; job flow is not waiting or running')

        self.address = jobflow.masterpublicdnsname
        return self.address

    def ls(self, path='*'):
        args = [
            'ssh', '-q',
            '-i', self.ec2_key_pair_file,
            'hadoop@%s' % self._address_of_master(),
            'find', self.root_path,
        ]
        p = subprocess.Popen(args,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        output, err = p.communicate()
        if err:
            log.error(str(err))
            raise LogFetchException('ssh error: %s' % str(err))
        else:
            for line in output.split('\n'):
                # skip directories, we only want to return downloadable files
                if line and not line.endswith('/'):
                    yield line

    def get(self, path):
        log_path = os.path.join(self.local_temp_dir, 'log')

        if self._uri_of_downloaded_log_file != path:
            log.debug('downloading %s -> %s' % (path, log_path))
            # download path to log_path

            self._uri_of_downloaded_log_file = path

        args = [
            'scp', '-q',
            '-i', self.ec2_key_pair_file,
            'hadoop@%s:%s' % (self._address_of_master(), path),
            log_path,
        ]
        p = subprocess.Popen(args,
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        output, err = p.communicate()
        if err:
            if 'not a regular file' in err:
                return None
            log.error(str(err))
            raise LogFetchException('scp error: %s' % str(err))
        else:
            return log_path
