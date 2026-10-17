import unittest

from zeus_pc.actions import GUID_RE


class ActionValidationTests(unittest.TestCase):
    def test_valid_guid(self):
        self.assertTrue(GUID_RE.fullmatch("381b4222-f694-41f0-9685-ff5bb260df2e"))

    def test_rejects_shell_text(self):
        self.assertFalse(GUID_RE.fullmatch("381b4222-f694-41f0-9685-ff5bb260df2e & whoami"))


if __name__ == "__main__":
    unittest.main()
