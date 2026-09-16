---
name: xrc-to-python-layout
description: Convert an Odemis wxPython XRC layout from src/odemis/gui/xmlh/resources into a maintainable pure-Python component under src/odemis/gui/layout. Use when converting, migrating, replacing, or reviewing an XRC GUI definition.
---

# Convert an Odemis XRC layout to Python

Convert one XRC top-level window at a time. Preserve its behavior and externally
used interface, but do not mechanically reproduce every generated attribute.

## Inputs

Identify:

- The source XRC file under `src/odemis/gui/xmlh/resources`.
- The corresponding generated class in `src/odemis/gui/main_xrc.py`.
- The controller, dialog, or tab that constructs or inherits from that class.
- The target module under `src/odemis/gui/layout/components`.

Do not modify unrelated layouts.

## Investigation

Before editing:

1. Read the complete top-level XRC object being converted.
2. Inspect its generated class in `src/odemis/gui/main_xrc.py`.
3. Search the repository for every use of the generated class.
4. Search controllers and tests for widget attributes accessed on the layout.
5. For custom XRC classes, inspect their resource handlers in
   `src/odemis/gui/xmlh/xh_delmic.py`.
6. Inspect existing converted components for established layout patterns.
7. Inspect the public theme API in
   `src/odemis/gui/layout/constants/themes.py` and how converted components use
   it.
8. Group repeated custom controls by constructor and styling options. When one
   instance reveals a conversion mismatch, inspect every call using the same
   helper or option instead of fixing only the reported control.

Create `self.<name>` attributes only for controls accessed outside the layout
class or required by wx lifecycle behavior. Keep purely structural panels,
sizers, labels, and spacers as local variables.

Do not expose every named XRC object merely because `pywxrc` generated an
attribute for it.

## Implementation

Create a class that inherits from the equivalent `wx.Panel`, `wx.Dialog`, or
`wx.Frame`.

The constructor must remain compatible with the existing call site. Use type
hints and build the layout immediately after initializing the wx base class.
Keep the constructor as a readable outline of the top-level visual structure.
Create every direct top-level sizer child through a named private builder
method, even when that builder currently creates only one control.

Preserve all relevant XRC behavior:

- Parent-child hierarchy.
- Control classes and constructor arguments.
- Styles and extra styles.
- Initial size and minimum size.
- Sizer orientation and nesting.
- Proportions, borders, flags, spacers, and growable rows or columns.
- Labels, values, ranges, selections, tooltips, and icons.
- Foreground and background colours.
- Initial visibility and enabled state.
- Scrolling configuration.
- Control ordering.
- Custom-control registration behavior from `xh_delmic.py`.
- Explicit layout calls required before controllers access calculated state.

Treat a custom button's face and foreground as separate properties. Do not
infer foreground solely from `face_colour`: different non-default faces can
still intentionally use either normal or contrasting text. Default-face
buttons must use the theme's normal button foreground.

Shared control builders should encode valid styling combinations. Reject an
invalid combination explicitly, such as contrasting text on a default button
face, so construction and focused tests catch future call-site mistakes.

Use `hbox()` and `vbox()` from
`odemis.gui.layout.util.sizers` for box sizers so indentation shows the visual
hierarchy. Instantiate other sizer types directly.

Split each top-level section into private builder methods and split large
sections further at meaningful visual boundaries. Do not create a method for
every individual widget.

Use local variables for implementation details. Assign a widget to `self` only
when another module accesses it, a test requires it, or the class itself needs
it after construction.

Call `SetName` only when code actually uses the wx name at runtime, such as
through `FindWindowByName`. A name in XRC alone is not sufficient reason.

## Strings

Search `src/odemis/gui/layout/constants/strings.py` before adding a string.

- Reuse an existing constant when the same user-visible text with the same
  meaning already exists.
- Add a constant only when text with the same meaning is demonstrably shared by
  multiple layout modules.
- Keep layout-specific, one-off text in the component.
- Do not extract text merely because it occurs multiple times within one
  component.
- Do not populate the constants module by copying labels from the XRC or
  generated class in bulk.
- Do not centralize empty labels, whitespace placeholders, axis symbols, or
  values whose meaning depends only on one widget.
- Preserve spelling, capitalization, punctuation, ellipses, and whitespace
  from the XRC unless the task explicitly changes the UI.

## Theme

Use `src/odemis/gui/layout/constants/themes.py` as the sole theme authority for
converted layouts. Inspect its public API and existing consumers before adding
theme data.

- Consume the public theme API instead of importing individual legacy colour
  constants from `odemis.gui`.
- Reuse existing semantic colours, font sizes, and spacing metrics from the
  selected theme.
- Pass theme font-size integers into component builders and helpers directly.
- Use `odemis.gui.layout.util.fonts.set_font` when a wx control needs an
  explicit font size or weight. Do not define component-local font mutation
  helpers.
- Preserve the control's native font family rather than constructing a new
  `wx.Font` solely to change its size.
- Do not introduce font-style wrapper objects when a semantic integer font
  size is sufficient.
- Use theme spacing metrics for recurring layout density roles so another
  theme can consistently provide a compact or spacious layout.
- Keep default-face button text on the theme's normal button foreground.
  Contrasting foreground text must be an explicit exception on a non-default
  button face; validate this invariant in shared button builders.
- Add a theme value only when it is genuinely reusable by multiple layouts or
  represents an application-wide visual role.
- Keep one-off dimensions, alignment offsets, and exceptional spacing local to
  the component.
- Do not add a theme field for every literal found in an XRC file.
- Do not turn widget configuration such as control width, gauge margin, or a
  section-specific indent into global theme API without demonstrated reuse.
- Do not make `themes.py` or converted components depend on
  `src/odemis/gui/__init__.py`.

Do not expand the theme object merely to eliminate all numeric or colour
literals.

Legacy colour constants in `src/odemis/gui/__init__.py` may be consulted only
as visual reference when establishing the default `DARK` theme. Reproduce
appropriate values under semantic theme roles without retaining an import or
runtime dependency. Updating or removing the legacy constants is outside an
XRC conversion task.

## Documentation and style

Follow the repository's Python and docstring conventions.

- Keep annotations compatible with Python 3.8 as shipped by Ubuntu 20.04.
  Use `Optional`, `Union`, and other `typing` forms instead of PEP 604 unions
  or built-in generic syntax.
- Use `Any` at wx typemap boundaries that accept several extension and Python
  representations, such as a size accepting tuples, `wx.Size`, and
  `wx.DefaultSize`.
- Give the class a short description of the represented layout.
- Do not include an `Attributes` section enumerating every widget.
- Document non-obvious compatibility requirements or wx behavior only.
- Use concise reStructuredText docstrings for functions and methods.
- Include `:param:` and `:return:` fields where useful, without repeating type
  information already present in annotations.
- Avoid decorative Unicode separators and generated-looking commentary.
- Comment only where the reason for a wx workaround is not apparent.

## Preview support

Add this guard to independently preview the component:

```python
if __name__ == "__main__":
    from odemis.gui.layout.util.preview import run_preview

    run_preview(ComponentClass)
```

Run it as a module:

```shell
python3 -m odemis.gui.layout.components.module_name
```

Use `ODEMIS_PREVIEW_SHOW_ALL=1` when hidden controls must be inspected. The
preview suppresses both `Show(False)` and `Hide()` calls while this flag is
enabled.

## Wiring

After implementing the component:

1. Export it from `src/odemis/gui/layout/components/__init__.py`.
2. Ensure it is available through `odemis.gui.layout` if that is the existing
   import convention.
3. Replace the relevant generated XRC constructor or base class at its call
   site.
4. Do not change unrelated call sites.
5. Keep the XRC source and generated class unless the task explicitly requests
   their removal. They remain useful for comparison during the migration.

## Validation

Before finishing:

1. Verify every externally accessed widget attribute exists with the same name
and compatible control type.
2. Verify structural widgets that are not externally accessed remain local.
3. Compare the Python hierarchy and sizer options against the XRC.
4. Check hidden and disabled initial states.
5. Check custom controls against their XRC resource handlers.
6. Check every call site of shared control builders for valid combinations of
   face, foreground, font, icon, size, and state. Do not stop after validating
   one representative control.
7. For button helpers, test at least one normal-text button and each supported
   contrasting-text style. Test that invalid style combinations are rejected.
8. Run the component preview and inspect resizing and scrolling.
9. Run the smallest relevant existing test.
10. Run `autopep8 --in-place --select W291,W292,W293,W391` on changed Python
   files.
11. Parse changed layout modules with Python 3.8 grammar or run them with the
   project's Ubuntu 20.04-compatible Python.
12. Confirm every direct top-level sizer child is created by a named builder.
13. Confirm font sizes come from `themes.py` where they represent shared
   semantic roles and are applied through
   `odemis.gui.layout.util.fonts.set_font`.
14. Confirm converted components and `themes.py` have no new dependency on
   legacy theme constants from `odemis.gui`.
15. Confirm there are no unused imports, copied attribute inventories, or
   unnecessary additions to `themes.py` and `strings.py`.

Report any behavior that could not be reproduced exactly.
