import unittest
import yaml

from odemis.odemisd.modelgen import SafeLoader


class SafeLoader_extensions_test(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        SafeLoader.add_constructor('!include', SafeLoader.include)
        # Overwrite construct_yaml_map with custom mapper
        SafeLoader.yaml_constructors['tag:yaml.org,2002:map'] = SafeLoader.construct_yaml_map
        cls.SafeLoader = SafeLoader

        with open("yaml-merger-combination-include-extend-expected-result.yaml", "r") as f:
            cls.expected_full_result = yaml.load(f, cls.SafeLoader)

    def test_include(self):
        """
        Basic test for the include keyword in a yaml file to include the content of a file as the value of a key.
        """
        # __init__ of the CL-detector
        with open("yaml-merger-include-complete-init-CL-Detector-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"]
        self.assertEqual(expected_result, data_found)

        # Full CL-detector component
        with open("yaml-merger-include-full-CL-Detector-component-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"CL Detector": self.expected_full_result["CL Detector"]}
        self.assertEqual(expected_result, data_found)

        # __init__ contains a setting which is overwritten of the CL-detector
        with open("yaml-merger-include-complete-overwrite-init-CL-Detector-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"].copy()  # Copy because a value is adjusted
        expected_result["settle_time"] = ["overwritten", "values", "are", "correctly", "stored"]
        self.assertEqual(expected_result, data_found)

        # __init__ of the CL-detector using a relative reference in the !include
        with open(
            "yaml-merger-relative-path-test/yaml-merger-include-complete-init-relative-path-CL-Detector-test.odm.yaml",
                "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"]
        self.assertEqual(expected_result, data_found)

        # __init__ of the CL-detector using an absolute reference in the !include
        with open(
            "yaml-merger-relative-path-test/yaml-merger-include-complete-init-relative-path-CL-Detector-test.odm.yaml",
                "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = self.expected_full_result["CL Detector"]["init"]
        self.assertEqual(expected_result, data_found)

        with self.assertRaises(FileNotFoundError):
            with open("yaml-merger-include-error-non-exist-file-in-init-CL-Detector-test.odm.yaml", 'r') as f:
                data_found = yaml.load(f, self.SafeLoader)

        with self.assertRaises(SyntaxError):
            with open("yaml-merger-include-error-reference-to-typo-in-init-CL-Detector-test.odm.yaml", 'r') as f:
                data_found = yaml.load(f, self.SafeLoader)

    def test_extend(self):
        """
        Basic test for the extend keyword in a yaml file to extend a dictionary with the content of a file.
        """
        # __init__ of the SEM Scan Interface
        with open("yaml-merger-extend-complete-init-SEM-Scan-Interface-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"]}
        self.assertEqual(expected_result, data_found)

        # Full SEM Scan Interface component
        with open("yaml-merger-extend-full-SEM-Scan-Interface-component-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"SEM Scan Interface": self.expected_full_result["SEM Scan Interface"]}
        self.assertEqual(expected_result, data_found)

        # __init__ contains a entries which are overwritten of the CL-detector
        with open("yaml-merger-extend-complete-init-overwrite-SEM-Scan-Interface-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"].copy()}
        expected_result["init"]["username"] = 'user1_overwritten'
        expected_result["init"]["password"] = 'complicated_and_overwritten'
        self.assertEqual(expected_result, data_found)

        # __init__ of the SEM Scan Interface using a relative reference in the !include
        with open("yaml-merger-relative-path-test/" +
                  "yaml-merger-extend-complete-init-relative-path-SEM-Scan-Interface-test.odm.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"init": self.expected_full_result["SEM Scan Interface"]["init"]}
        self.assertEqual(expected_result, data_found)

        with self.assertRaises(FileNotFoundError):
            with open("yaml-merger-extend-error-non-exist-file-in-init-SEM-Scan-interface-test.odm.yaml", 'r') as f:
                data_found = yaml.load(f, self.SafeLoader)

        with self.assertRaises(SyntaxError):
            with open("yaml-merger-extend-error-reference-to-typo-in-init-SEM-Scan-Interface.odm.yaml", 'r') as f:
                data_found = yaml.load(f, self.SafeLoader)

    def test_combination_include_extend_two_components_dict(self):
        """
        Extended test combining the extend and include keywords in a file to a single dict.
        """
        # CL Detector and SEM Scan Interface component
        with open("yaml-merger-combination-two-components.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        expected_result = {"SEM Scan Interface": self.expected_full_result["SEM Scan Interface"],
                           "CL Detector": self.expected_full_result["CL Detector"]}
        self.assertEqual(expected_result, data_found)

    def test_multiple_layered_include_extend_realistic_startup_file_combination(self):
        """
        Extended test combining the extend and include keywords in a file to a complete microscope startup file
        """
        # Full startup setting defined in yaml-merger-combination-include-extend-expected-result.yaml
        with open("yaml-merger-combination-realistic-startup-file.yaml", "r") as f:
            data_found = yaml.load(f, self.SafeLoader)

        # Compare with expected results
        self.assertEqual(self.expected_full_result, data_found)

if __name__ == '__main__':
    unittest.main()
