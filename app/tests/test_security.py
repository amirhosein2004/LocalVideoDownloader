import os
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from app import main


class PathsAndUrls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'downloads'
        self.root.mkdir()
        self.patches = [patch.object(main,'ROOT',self.root),patch.object(main,'HOST_ROOT','C:/Users/Test/Downloads')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_windows_absolute_and_relative_paths(self):
        self.assertEqual(main.destination('c:\\users\\test\\Downloads\\My Videos'), self.root/'My Videos')
        self.assertEqual(main.destination('Courses/DevOps'), self.root/'Courses/DevOps')

    def test_traversal_and_unmounted_paths(self):
        for value in ['../outside','/etc','C:/Users/Test/Downloads-other','C:/Users/Test/Downloads/../../outside','bad\x00path']:
            with self.subTest(value=value), self.assertRaises(HTTPException):
                main.destination(value)

    def test_symlink_escape(self):
        (self.root/'escape').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(HTTPException):
            main.destination('escape/outside')

    def test_linux_case_sensitivity(self):
        with patch.object(main,'HOST_ROOT','/home/Test/Downloads'):
            self.assertEqual(main.destination('/home/Test/Downloads/Courses'), self.root/'Courses')
            with self.assertRaises(HTTPException):
                main.destination('/home/test/Downloads/Courses')

    def test_host_path_formats(self):
        for host, absolute in [
            ('C:/Users/Test/Downloads', r'C:\Users\Test\Downloads\My Videos'),
            ('/home/test/Downloads', '/home/test/Downloads/My Videos'),
            ('/Users/test/Downloads', '/Users/test/Downloads/My Videos'),
            ('//server/share/Downloads', r'\\server\share\Downloads\My Videos'),
        ]:
            with self.subTest(host=host), patch.object(main, 'HOST_ROOT', host):
                self.assertEqual(main.destination(absolute), self.root/'My Videos')
                self.assertEqual(main.destination(''), self.root)
                self.assertEqual(main.destination(host + '/'), self.root)
                self.assertEqual(main.destination('Courses/DevOps'), self.root/'Courses/DevOps')
                with self.assertRaises(HTTPException):
                    main.destination(host + '/../outside')

    def test_clear_history_keeps_files_and_active_jobs(self):
        database = Path(self.temp.name)/'history.sqlite3'
        video = self.root/'video.mp4'
        video.write_bytes(b'keep file')
        records = [('done','completed'),('bad','failed'),('cancel','cancelled'),
                   ('interrupted','interrupted'),('active','running'),('waiting','queued'),('stopping','cancelled')]
        with sqlite3.connect(database) as con:
            con.execute('CREATE TABLE jobs (id TEXT, status TEXT)')
            con.executemany('INSERT INTO jobs VALUES (?,?)', records)
        with patch.object(main, 'DB', database), patch.object(main, 'processes', {'stopping': object()}):
            self.assertEqual(main.clear_history(), {'removed': 4})
            self.assertEqual(main.clear_history(), {'removed': 0})
        with sqlite3.connect(database) as con:
            self.assertEqual({r[0] for r in con.execute('SELECT id FROM jobs')}, {'active','waiting','stopping'})
        self.assertEqual(video.read_bytes(), b'keep file')

    def test_invalid_url_forms(self):
        for value in ['file:///etc/passwd','ftp://example.com/v','https://user:secret@example.com/v','http://x:bad','https://example.com/\nheader','http://[broken']:
            with self.subTest(value=value), self.assertRaises(HTTPException):
                main.validate_url(value)

    def test_private_and_mixed_dns_are_rejected(self):
        with patch.dict(os.environ, {'ALLOW_PRIVATE_SOURCES':'0'}):
            for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1']:
                addresses = [(socket.AF_INET,socket.SOCK_STREAM,6,'',(ip,443))]
                with self.subTest(ip=ip), patch.object(socket,'getaddrinfo',return_value=addresses), self.assertRaises(HTTPException):
                    main.validate_url('https://example.com/video')
            public = (socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443))
            private = (socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))
            with patch.object(socket,'getaddrinfo',return_value=[public,private]), self.assertRaises(HTTPException):
                main.validate_url('https://example.com/video')
            with patch.object(socket,'getaddrinfo',return_value=[public]):
                self.assertEqual(main.validate_url('https://example.com/video'),'https://example.com/video')

    def test_snapshot_does_not_query_unchanged_database(self):
        with patch.object(main,'snapshot_revision',main.revision), patch.object(main,'snapshot_rows',[{'id':'cached'}]), patch.object(main,'db',side_effect=AssertionError('Unexpected query')):
            for _ in range(100):
                self.assertEqual(main.jobs(),[{'id':'cached'}])


if __name__=='__main__':
    unittest.main()
