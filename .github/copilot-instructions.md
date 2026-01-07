# Style
For Python code, always use type hints for function parameters and return types.
Include docstrings for all functions and classes, following the reStructuredText style guide (but without the type information).

Code should be valid for Python 3.10 and above.

Clean-up code at the end of a task with:
`autopep8 --in-place --select W291,W292,W293,W391`

# Test cases
Run tests with such template command:
`env TEST_NOHW=1 python3 src/odemis/.../name_of_the_test_file.py TestCaseClassName.test_method_name`
