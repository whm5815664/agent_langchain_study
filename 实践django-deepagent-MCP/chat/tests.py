# Django 测试占位（本项目暂无自动化测试）。
from django.test import SimpleTestCase


class SmokeTests(SimpleTestCase):
    def test_placeholder(self):
        self.assertTrue(True)
