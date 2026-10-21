# Style
For Python code, always use type hints for function parameters and return types.
Test case methods (which take no argument besides `self` and return None) don't need type hints.

Include docstrings for all functions and classes, using the reStructuredText field format:
`:param name: description`, `:returns: description`, and `:raises ExceptionType: description`.
Do not put the type information in the docstring (it is in the type hints).
Do not use any other inline formatting markers (no backticks, no asterisks, no Markdown) in docstrings.

Code should be valid for Python 3.8 and above.

For file paths, use `pathlib.Path`, unless interfacing with existing code that uses `os.path` strings.

Clean-up code at the end of a task with:
`autopep8 --in-place --select W291,W292,W293,W391`

# Test cases
Run tests with such template command:
`env TEST_NOHW=1 python3 src/odemis/.../name_of_the_test_file.py TestCaseClassName.test_method_name`

For GUI test cases, when running them add NOMANUAL=1 to the environment, so that it automatically ends
after the test.

Keep tests short and focused:
- Add only the few test cases needed to cover the new behaviour: the normal case, plus the important edge cases or error cases. Do not test every parameter combination.
- One test method checks one behaviour, in a few lines. Do not write long setup code or repeat the same checks in several tests.
- Prefer extending an existing test file and TestCase class over creating new ones.
- Do not write tests for trivial code (getters, setters, simple wrappers).

Prefer simulators over mocks:
- Odemis provides simulated hardware: `odemis.driver.simulated` (Stage, Light, Chamber, GenericComponent...), `simcam`, `simsem`, and the simulated microscope files in `install/linux/usr/share/odemis/sim/*.odm.yaml`. Use them, directly or via the `model` instantiation, whenever possible.
- Only use `unittest.mock` when no simulator can reproduce the situation (e.g., hardware failure or a timeout). Never mock the code under test, and never mock Odemis components (Components, VigilantAttributes, Events) when a simulated one exists.
- Before writing a mock, check existing tests of similar code to see which simulator they use.

# Documentation
Documentation about the project (architecture, concepts, development guidelines) can be found in `doc/develop/*.rst`.
