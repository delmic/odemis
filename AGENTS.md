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

# Documentation
Documentation about the project (architecture, concepts, development guidelines) can be found in `doc/develop/*.rst`.
