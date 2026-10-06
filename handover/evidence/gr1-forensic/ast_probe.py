"""Read-only AST probe: the installed tree-sitter-c-sharp 0.23.5 grammar, no model."""
import tree_sitter_c_sharp as tscs
from tree_sitter import Language, Parser

LANG = Language(tscs.language())
parser = Parser(LANG)
SRC = (b"class R : S { int A() { return Get<int>(\"port\"); } int B() { return this.Get<int>(\"port\"); }"
       b" int E() { return Make<int>(); } string C() { return GetRaw(\"host\"); } }")
tree = parser.parse(SRC)
fields = [LANG.field_name_for_id(i) for i in range(1, LANG.field_count + 1)]
print("grammar field names containing name/ident/type:", sorted(f for f in fields if f and any(s in f for s in ("name", "ident", "type"))))


def describe(node, depth=0, field=None):
    pad = "  " * depth
    text = SRC[node.start_byte:node.end_byte].decode()
    print(f"{pad}{node.type}{' [field=' + field + ']' if field else ''}  text={text!r}")
    cursor = node.walk()
    if cursor.goto_first_child():
        while True:
            child = cursor.node
            if child.is_named:
                describe(child, depth + 1, cursor.field_name)
            if not cursor.goto_next_sibling():
                break


def calls(node):
    if node.type == "invocation_expression":
        yield node
    for child in node.children:
        yield from calls(child)


for inv in calls(tree.root_node):
    print("=" * 60)
    describe(inv)
    fn = inv.child_by_field_name("function")
    target = fn.child_by_field_name("name") if fn.type == "member_access_expression" else fn
    if target is not None and target.type == "generic_name":
        probe = {f: (lambda n: None if n is None else (n.type, SRC[n.start_byte:n.end_byte].decode()))(target.child_by_field_name(f))
                 for f in ("name", "identifier", "type_arguments", "type_argument_list")}
        print("generic_name.child_by_field_name ->", probe)
        print("generic_name children (type, field):",
              [(target.children[i].type, target.field_name_for_child(i)) for i in range(target.child_count)])
