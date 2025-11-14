# rope.py
# Full AVL-balanced Rope with 1 KB leaves

LEAF_SIZE = 1024  # 1 KB leaves


class RopeLeaf:
    def __init__(self, text):
        self.text = text  # Python string
        self.weight = len(text)

    def is_leaf(self):
        return True


class RopeNode:
    def __init__(self, left, right):
        self.left = left
        self.right = right
        self.weight = left.weight if left else 0
        self.recalc()

    def recalc(self):
        self.weight = (self.left.weight if self.left else 0)
        self.total = (
            (self.left.total if hasattr(self.left, "total") else self.left.weight if self.left else 0) +
            (self.right.total if hasattr(self.right, "total") else self.right.weight if self.right else 0)
        )

    def is_leaf(self):
        return False


def height(node):
    if node is None:
        return 0
    return getattr(node, "h", 1)


def update_height(node):
    node.h = max(height(node.left), height(node.right)) + 1


def balance_factor(node):
    return height(node.left) - height(node.right)


def rotate_right(y):
    x = y.left
    T = x.right
    x.right = y
    y.left = T
    y.recalc()
    x.recalc()
    update_height(y)
    update_height(x)
    return x


def rotate_left(x):
    y = x.right
    T = y.left
    y.left = x
    x.right = T
    x.recalc()
    y.recalc()
    update_height(x)
    update_height(y)
    return y


def balance(node):
    if node is None:
        return None

    update_height(node)

    bf = balance_factor(node)
    if bf > 1:
        if balance_factor(node.left) < 0:
            node.left = rotate_left(node.left)
        return rotate_right(node)
    if bf < -1:
        if balance_factor(node.right) > 0:
            node.right = rotate_right(node.right)
        return rotate_left(node)
    return node


def concat(a, b):
    if a is None:
        return b
    if b is None:
        return a
    n = RopeNode(a, b)
    update_height(n)
    return balance(n)


def split(node, index):
    if node is None:
        return None, None

    if node.is_leaf():
        left = node.text[:index]
        right = node.text[index:]
        return RopeLeaf(left) if left else None, RopeLeaf(right) if right else None

    if index < node.weight:
        left1, left2 = split(node.left, index)
        return left1, concat(left2, node.right)
    else:
        right1, right2 = split(node.right, index - node.weight)
        return concat(node.left, right1), right2


def flatten(node, out):
    if node is None:
        return
    if node.is_leaf():
        out.append(node.text)
    else:
        flatten(node.left, out)
        flatten(node.right, out)


class Rope:
    def __init__(self, initial=""):
        if initial:
            self.root = RopeLeaf(initial)
        else:
            self.root = None

    def __len__(self):
        if not self.root:
            return 0
        return getattr(self.root, "total", self.root.weight)

    def insert(self, index, text):
        left, right = split(self.root, index)
        new_leafs = []
        for i in range(0, len(text), LEAF_SIZE):
            new_leafs.append(RopeLeaf(text[i:i + LEAF_SIZE]))
        mid = None
        for leaf in new_leafs:
            mid = concat(mid, leaf)
        self.root = concat(concat(left, mid), right)

    def delete(self, index, length):
        left, rest = split(self.root, index)
        _, right = split(rest, length)
        self.root = concat(left, right)

    def substring(self, start, end):
        left, rest = split(self.root, start)
        mid, right = split(rest, end - start)
        out = []
        flatten(mid, out)
        return "".join(out)

    def get_text(self):
        out = []
        flatten(self.root, out)
        return "".join(out)
