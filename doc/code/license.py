from docutils import nodes
from sphinx.util.compat import Directive
from docutils import nodes

def make_license(node_class, name, arguments, options, content, lineno,
                    content_offset, block_text, state, state_machine):
    text = '\n'.join(content)
    license_node = node_class(text)
    if arguments:
        title_text = arguments[0]
        textnodes, messages = state.inline_text(title_text, lineno)
        license_node += nodes.title(title_text, '', *textnodes)
        license_node += messages
        if 'class' in options:
            classes = options['class']
        else:
            classes = ['license']
        license_node['classes'] += classes
    state.nested_parse(content, content_offset, license_node)
    return [license_node]

class LicenseDirective(Directive):

    # this enables content in the directive
    has_content = True

    def run(self):
        env = self.state.document.settings.env

        targetid = "license-%d" % env.new_serialno('license')
        targetnode = nodes.target('', '', ids=[targetid])

        ad = make_license(license, self.name, ['license'], self.options,
                             self.content, self.lineno, self.content_offset,
                             self.block_text, self.state, self.state_machine)

        if not hasattr(env, 'license_all_licenses'):
            env.license_all_licenses = []
        env.license_all_licenses.append({
            'docname': env.docname,
            'lineno': self.lineno,
            'license': ad[0].deepcopy(),
            'target': targetnode,
        })

        return [targetnode] + ad

class license(nodes.Admonition, nodes.Element):
    pass

def visit_license_node(self, node):
    self.visit_admonition(node)

def depart_license_node(self, node):
    self.depart_admonition(node)

def setup(app):
    app.add_node(license,
                 html=(visit_license_node, depart_license_node),
                 latex=(visit_license_node, depart_license_node),
                 text=(visit_license_node, depart_license_node))

    app.add_directive('license', LicenseDirective)
