# -*- coding: utf-8 -*-

"""
@author: Nandish Patel

Copyright © 2024 Nandish Patel, Delmic

This file is part of Odemis.

Odemis is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License version 2 as published by the Free
Software Foundation.

Odemis is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License along with
Odemis. If not, see http://www.gnu.org/licenses/.

"""
import logging
import math
from enum import IntEnum
from typing import Callable, List

import wx
import wx.lib.newevent
from wx.lib.agw.customtreectrl import CustomTreeCtrl, GenericTreeItem

from odemis.gui import BG_COLOUR_MAIN, FG_COLOUR_DIS, FG_COLOUR_MAIN, img
from odemis.gui.comp import buttons
from odemis.gui.cont.fastem_project_grid_base import DEFAULT_PARENT


class NodeChangeType(IntEnum):
    NAME_CHANGE = 1
    ADD_CHILD = 2
    REMOVE_CHILD = 3
    PARENT_CHANGE = 4
    DELETE_NODE = 5
    SORT_CHILDREN = 6
    CHECKBOX = 7


class NodeType(IntEnum):
    ALL_PROJECTS = 0
    PROJECT = 1
    TOA = 2
    RIBBON = 3
    SECTION = 4
    ROA = 5


TreeNodeChangeEvent, EVT_TREE_NODE_CHANGE = wx.lib.newevent.NewEvent()
# Define a mapping to determine the sorting precedence of different node types.
# Lower numerical values indicate higher priority in the sort order:
TYPE_PRIORITY = {NodeType.TOA: 1, NodeType.RIBBON: 2, NodeType.SECTION: 3, NodeType.ROA: 4}


class FastEMTreeNode:
    """Represents a node in a tree structure used for managing projects, sections, ribbons, roas and related data."""

    def __init__(self, name: str, type: int, row=None):
        """
        Initializes a new node in the tree.

        :param name: (str) The name of the node.
        :param type: (int) The type of the node.
        :param row: (object, optional) The row associated with this node.
        """
        self.name = name
        self.type = type
        self.parent_node = None
        self.row = row
        self.children = []
        self._on_change_callback: Callable = None  # Callback function to notify changes
        if self.row:
            self.row.roa.name.subscribe(self._on_name_change)
            self.row.roa.slice_index.subscribe(self._on_slice_index_change)
            if self.row.parent_name is not None:
                self.parent_name = self.row.parent_name.value
                self.row.parent_name.subscribe(self._on_parent_name_change)

    def set_on_change_callback(self, callback: Callable):
        """
        Sets the callback function to notify changes.

        :param callback: (Callable) The callback function to be called on changes.
        """
        self._on_change_callback = callback

    def _on_parent_name_change(self, new_parent_name: str):
        """
        Handles changes to the parent name, updating the node's parent and notifying the change.

        :param new_parent_name: (str) The new parent name.
        """
        project_node = self.project_node()

        if self.parent_node:
            # Remove from current parent's children list
            self.parent_node.remove_child(self)
            self.parent_node = None  # Clear parent reference to maintain tree integrity

        if new_parent_name == DEFAULT_PARENT:
            new_parent_node = project_node
        else:
            # Find new parent node from the project node
            new_parent_node = project_node.find_node(new_parent_name)

        if new_parent_node:
            # Add to new parent's children list
            new_parent_node.add_child(self)
            self.parent_node = new_parent_node  # Update parent reference
            self.parent_name = new_parent_name
            if self._on_change_callback:
                self._on_change_callback(NodeChangeType.PARENT_CHANGE, self)
            project_node.sort_children_recursively()
        else:
            raise ValueError(f"Cannot find node with name {new_parent_name}")

    def find_parent_node_by_type(self, node_type: NodeType):
        """
        Finds the closest ancestor node of a specified type.

        :param node_type: (NodeType) The type of the ancestor node to find.
        :return: (FastEMTreeNode) The closest ancestor node of the specified type, or None if not found.
        """
        node = self
        while node.parent_node and node.type != node_type:
            node = node.parent_node
        return node if node.type == node_type else None

    def project_node(self):
        """
        Find the project node.

        :return: (FastEMTreeNode) The project node.
        """
        node = self
        while node.parent_node and node.type != NodeType.PROJECT:
            node = node.parent_node
        return node

    def root_node(self):
        """
        Finds the root node of the entire tree.

        :return: (FastEMTreeNode) The root node of the tree.
        """
        node = self
        while node.parent_node:
            node = node.parent_node
        return node

    def _on_name_change(self, name: str):
        """
        Handles changes to the node's name, updating the name and notifying the change.

        :param name: (str) The new name of the node.
        """
        self.name = f"{name}_{self.row.roa.slice_index.value}"
        if self._on_change_callback:
            self._on_change_callback(NodeChangeType.NAME_CHANGE, self)

    def _on_slice_index_change(self, slice_index: int):
        """
        Handles changes to the slice index, updating the node's name and notifying the change.

        :param slice_index: (int) The new slice index.
        """
        self.name = f"{self.row.roa.name.value}_{slice_index}"
        if self._on_change_callback:
            self._on_change_callback(NodeChangeType.NAME_CHANGE, self)

    def rename(self, name: str):
        """
        Renames the node and notifies the change.

        :param name: (str) The new name of the node.
        """
        self.name = name
        if self._on_change_callback:
            self._on_change_callback(NodeChangeType.NAME_CHANGE, self)

    def add_child(self, child, sort: bool = True):
        """
        Adds a child node to the current node.

        :param child: (FastEMTreeNode) The child node to add.
        :param sort: (bool) Sort the order of FastEMTreeNode's children.
        :raises ValueError: If the child type is not allowed under the current node's type.
        """
        if self.can_have_child(child.type):
            self.children.append(child)
            child.parent_node = self  # Set child's parent node to current node
            if self._on_change_callback:
                self._on_change_callback(NodeChangeType.ADD_CHILD, child)
                if sort:
                    self.sort_children_recursively()
        else:
            raise ValueError(
                f"Cannot add child of type {child.type} to parent of type {self.type}"
            )

    def remove_child(self, child, sort: bool = True):
        """
        Removes a child node from the current node.

        :param child: (FastEMTreeNode) The child node to remove.
        :param sort: (bool) Sort the order of FastEMTreeNode's children.
        :raises ValueError: If the child is not found in the current node's children.
        """
        if child in self.children:
            self.children.remove(child)
            if self._on_change_callback:
                self._on_change_callback(NodeChangeType.REMOVE_CHILD, child)
                if sort:
                    self.sort_children_recursively()
        else:
            raise ValueError("Child not found in the node's children")

    def can_have_child(self, child_type: NodeType) -> bool:
        """
        Checks if a child of the given type can be added to the current node.

        :param child_type: (NodeType) The type of the child node.
        :return: (bool) True if the child type can be added, False otherwise.
        """
        if self.type == NodeType.ALL_PROJECTS:
            return child_type == NodeType.PROJECT
        elif self.type == NodeType.PROJECT:
            return child_type in (NodeType.TOA, NodeType.RIBBON, NodeType.SECTION, NodeType.ROA)
        elif self.type == NodeType.RIBBON:
            return child_type == NodeType.SECTION
        elif self.type == NodeType.SECTION:
            return child_type == NodeType.ROA
        else:
            return False

    def find_node(self, name: str):
        """
        Finds a node by its name.

        :param name: (str) The name of the node to find.
        :return: (FastEMTreeNode) The node with the specified name, or None if not found.
        """
        if self.name == name:
            return self
        for child in self.children:
            found = child.find_node(name)
            if found:
                return found
        return None

    def delete_node(self, name: str) -> bool:
        """
        Deletes a node by its name.

        :param name: (str) The name of the node to delete.
        :return: (bool) True if the node was successfully deleted, False otherwise.
        """
        for i, child in enumerate(self.children):
            if child.name == name:
                # If the child to be deleted is found
                if child.children:
                    # If the child node has children, reassign them
                    parent_node = child.parent_node
                    for grandchild in child.children:
                        if parent_node and parent_node.can_have_child(grandchild.type):
                            parent_node.add_child(grandchild)
                # Remove the child node from the children list
                if self._on_change_callback:
                    self._on_change_callback(NodeChangeType.DELETE_NODE, child)
                del self.children[i]
                return True

            # Recursively call delete_node on each child
            if child.delete_node(name):
                return True
        return False

    def delete_node_by_shape(self, shape) -> bool:
        """
        Deletes a node by its shape.

        :param shape: (EditableShape) The shape associated with the node to delete.
        :return: (bool) True if the node was successfully deleted, False otherwise.
        """
        for i, child in enumerate(self.children):
            if child.row and child.row.roa.shape == shape:
                # If the child to be deleted is found
                if child.children:
                    # If the child node has children, reassign them
                    parent_node = child.parent_node
                    for grandchild in child.children:
                        if parent_node and parent_node.can_have_child(grandchild.type):
                            parent_node.add_child(grandchild)
                # Remove the child node from the children list
                if self._on_change_callback:
                    self._on_change_callback(NodeChangeType.DELETE_NODE, child)
                del self.children[i]
                return True

            # Recursively call delete_node_by_shape on each child
            if child.delete_node_by_shape(shape):
                return True
        return False

    def get_children_names(self) -> List[str]:
        """
        Gets the names of all child nodes.

        :return: (list) List of names of all child nodes.
        """
        return [child.name for child in self.children]

    def get_all_nodes(self) -> List:
        """
        Gets a list of all nodes from the current node.

        :return: (list) List of all FastEMTreeNode nodes from the current node.
        """
        all_nodes = [self]
        for child in self.children:
            all_nodes.extend(child.get_all_nodes())
        return all_nodes

    def get_depth(self) -> int:
        """
        Gets the depth of the node in the tree.

        :return: (int) The depth of the node.
        """
        if not self.children:
            return 1
        else:
            return 1 + max(child.get_depth() for child in self.children)

    def is_leaf(self) -> bool:
        """
        Checks if the node is a leaf (i.e., it has no children).

        :return: (bool) True if the node is a leaf, False otherwise.
        """
        return not self.children

    def find_nodes_by_type(self, type: NodeType) -> List:
        """
        Finds all nodes of a specific type.

        :param type: (NodeType) The type of nodes to find.
        :return: (list) List of FastEMTreeNode nodes of the specified type.
        """
        nodes = []
        if self.type == type:
            nodes.append(self)
        for child in self.children:
            nodes.extend(child.find_nodes_by_type(type))
        return nodes

    def print_tree(self, level: int = 0):
        """
        Logs the tree structure starting from the current node.

        :param level: (int) The current depth level in the tree (used for indentation).
        """
        indent = " " * level * 2
        logging.info(f"{indent}{self.name} (type: {self.type.name})")
        for child in self.children:
            child.print_tree(level + 1)

    def sort_children_recursively(self):
        """
        Sorts children nodes recursively based on their type and index.

        The sorting is done using a predefined type priority and the index of the nodes' rows.
        """

        def get_sort_key(node):
            row_index = node.row.index if node.row else math.inf
            return (TYPE_PRIORITY.get(node.type, 0), row_index, node.name)

        # Sort current node's children
        self.children.sort(key=get_sort_key)

        # Recursively sort children
        for child in self.children:
            child.sort_children_recursively()

        if self._on_change_callback:
            self._on_change_callback(NodeChangeType.SORT_CHILDREN, self)


class NodeWindow(wx.Window):
    """A wx.Window subclass that represents a window for displaying and interacting with a tree node."""

    def __init__(self, parent, node, *args, **kwargs):
        """
        Initializes a NodeWindow instance.

        :param parent: (wx.Window) The parent window.
        :param node: (FastEMTreeNode) The node associated with this window.
        """
        super().__init__(parent, style=wx.NO_BORDER, size=(300, 30), *args, **kwargs)
        self.node = node

        self.SetBackgroundColour(BG_COLOUR_MAIN)
        self.SetForegroundColour(FG_COLOUR_MAIN)
        self.SetFont(parent.GetFont())

        # Create sizers
        self.main_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.left_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.right_sizer = wx.BoxSizer(wx.HORIZONTAL)

        # Create widgets
        self.checkbox = wx.CheckBox(self, label="")
        self.visibility_btn = buttons.ImageToggleButton(self,
                                                        bitmap=img.getBitmap("icon/ico_eye_closed.png"))
        self.visibility_btn.bmpHover = img.getBitmap("icon/ico_eye_closed_h.png")
        self.visibility_btn.bmpSelected = img.getBitmap("icon/ico_eye_open.png")
        self.visibility_btn.bmpSelectedHover = img.getBitmap("icon/ico_eye_open_h.png")
        self.visibility_btn.SetToolTip("Toggle acquisition visibility")
        self.item_label = wx.StaticText(self, label=node.name)
        self.gauge = wx.Gauge(self, range=100, size=(100, 16))
        self.status_text = wx.StaticText(self, label="Open")
        self.status_text.SetForegroundColour(FG_COLOUR_DIS)

        # Bind the checkbox event
        self.checkbox.Bind(wx.EVT_CHECKBOX, self.on_checkbox)

        if node.type in [NodeType.ALL_PROJECTS, NodeType.PROJECT, NodeType.RIBBON]:
            self.gauge.Hide()
            self.status_text.Hide()
        # By default hide visibility_btn for all node types, later shown and used for NodeType.TOA
        self.visibility_btn.Hide()
        # Layout widgets
        self._layout_widgets()

    def _layout_widgets(self):
        """
        Layouts the widgets within the window using sizers.
        """
        self.left_sizer.Add(self.checkbox, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.left_sizer.Add(self.visibility_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)
        self.left_sizer.Add(self.item_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 5)

        self.main_sizer.Add(self.left_sizer, 0, wx.ALIGN_LEFT)
        self.main_sizer.AddStretchSpacer(1)
        self.right_sizer.Add(self.gauge, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)
        self.right_sizer.Add(self.status_text, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 10)

        self.main_sizer.Add(self.right_sizer, 0)
        self.SetSizer(self.main_sizer)
        self.Layout()

    def on_checkbox(self, evt):
        """Handles the event when the checkbox state changes."""
        checked = self.checkbox.IsChecked()
        self.propagate_checkbox_state(checked)

    def propagate_checkbox_state(self, checked: bool):
        """
        Propagates the checkbox state to all child nodes' windows.

        :param checked: (bool) The new checkbox state.
        """
        if self.node.children:
            for child_node in self.node.children:
                child_window = self.GetParent().node_window.get(child_node)
                if child_window:
                    child_window.checkbox.SetValue(checked)
                    child_window.propagate_checkbox_state(checked)
        self.GetParent().trigger_node_change_event(NodeChangeType.CHECKBOX)


class FastEMProjectTreeCtrl(CustomTreeCtrl):
    """A custom tree control for displaying and managing a hierarchical structure of nodes."""

    def __init__(self, parent):
        super().__init__(
            parent,
            agwStyle=wx.TR_DEFAULT_STYLE
            | wx.TR_HAS_VARIABLE_ROW_HEIGHT
            | wx.TR_NO_BUTTONS,
            size=(300, 650),
        )

        self.SetBackgroundColour(BG_COLOUR_MAIN)
        self.SetForegroundColour(FG_COLOUR_MAIN)
        self._initialized = False

        # Initialize to store node-window mappings
        self.node_window = {}

    def populate_tree_from_root_node(self, root_node: FastEMTreeNode):
        """
        Populate or update the tree control with nodes starting from the root node.

        :param root_node: (FastEMTreeNode) The root node of the tree to populate.
        """
        self.DeleteAllItems()  # Clear existing items
        root_item = self.AddRoot("")
        self.add_widgets_to_item(root_item, root_node)
        self.SetPyData(root_item, root_node)
        root_node.set_on_change_callback(self._on_tree_node_change)
        self._update_or_add_items(root_node, root_item)
        self.ExpandAll()

    def get_all_items(self) -> List[GenericTreeItem]:
        """
        Retrieve all items in the tree control.

        :return: List of GenericTreeItem representing all items in the tree.
        """

        def _get_tree_items(item):
            items = []
            if item:
                items.append(item)
                child, cookie = self.GetFirstChild(item)
                while child and child.IsOk():
                    items.extend(_get_tree_items(child))
                    child, cookie = self.GetNextChild(item, cookie)
            return items

        root = self.GetRootItem()
        return _get_tree_items(root)

    def _update_or_add_items(self, node: FastEMTreeNode, parent_item: GenericTreeItem):
        """
        Update or add tree items recursively based on changes in the node.

        :param node: (FastEMTreeNode) The node to update or add.
        :param parent_item: (GenericTreeItem) The parent item in the tree control.
        """
        # Recursively add children
        for child_node in node.children:
            child_item = self.AppendItem(parent_item, "")
            self.add_widgets_to_item(child_item, child_node)
            self.SetPyData(child_item, child_node)
            child_node.set_on_change_callback(self._on_tree_node_change)
            # Recurse to add grandchildren
            self._update_or_add_items(child_node, child_item)

    def trigger_node_change_event(self, change_type: NodeChangeType):
        """
        Trigger a node change event.

        :param change_type: (NodeChangeType) The type of change to notify.
        """
        event = TreeNodeChangeEvent(change_type=change_type)
        wx.PostEvent(self, event)

    def _on_tree_node_change(self, change_type: NodeChangeType, node: FastEMTreeNode):
        """
        Handle changes in the tree node and update the tree control accordingly.

        :param change_type: (NodeChangeType) The type of change that occurred.
        :param node: (FastEMTreeNode) The node that changed.
        """
        if change_type == NodeChangeType.NAME_CHANGE:
            self._update_node_name(node)
        elif change_type == NodeChangeType.ADD_CHILD:
            self._add_tree_node(node)
        elif change_type in (NodeChangeType.REMOVE_CHILD, NodeChangeType.DELETE_NODE):
            self._remove_tree_node(node)
        elif change_type == NodeChangeType.PARENT_CHANGE:
            self._reparent_tree_node(node)
        elif change_type == NodeChangeType.SORT_CHILDREN:
            self._sort_children_recursively(node)
        self.trigger_node_change_event(change_type)

    def _update_node_name(self, node: FastEMTreeNode):
        """
        Update the name of a node in the tree control.

        :param node: (FastEMTreeNode) The node whose name has changed.
        """
        item = self._find_item_by_node(node)
        if item:
            window = self.GetItemWindow(item)
            window.item_label.SetLabel(node.name)
            window.Layout()

    def _add_tree_node(self, node: FastEMTreeNode):
        """
        Add a new node to the tree control.

        :param node: (FastEMTreeNode) The node to add.
        """
        parent_item = self._find_item_by_node(node.parent_node)
        if parent_item:
            child_item = self.AppendItem(parent_item, "")
            self.add_widgets_to_item(child_item, node)
            self.SetPyData(child_item, node)
            node.set_on_change_callback(self._on_tree_node_change)
            self.Expand(parent_item)

    def _remove_tree_node(self, node: FastEMTreeNode):
        """
        Remove a node from the tree control.

        :param node: (FastEMTreeNode) The node to remove.
        """
        item = self._find_item_by_node(node)
        if item:
            self.Delete(item)
            del self.node_window[node]

    def _reparent_tree_node(self, node: FastEMTreeNode):
        """
        Reparent a node in the tree control.

        :param node: (FastEMTreeNode) The node to reparent.
        """
        # Find the current item and store its subtree
        self._remove_tree_node(node)
        for node in node.get_all_nodes():
            self._add_tree_node(node)

    def OnCompareItems(self, item1, item2):
        """
        Override to change the sort order of items in the tree control.
        """
        node1 = self.GetPyData(item1)
        node2 = self.GetPyData(item2)

        # Compare by type priority, then row index, then name
        # Step 1: Compare by type priority.
        # Nodes with a higher priority value (lower numerical value) come first.
        if TYPE_PRIORITY.get(node1.type, 0) != TYPE_PRIORITY.get(node2.type, 0):
            return TYPE_PRIORITY.get(node1.type, 0) - TYPE_PRIORITY.get(node2.type, 0)
        # Step 2: If types are the same, compare by row index (if both nodes have a row).
        # Lower row index comes first.
        elif (node1.row and node2.row) and node1.row.index != node2.row.index:
            return node1.row.index - node2.row.index
        # Step 3: If types and row indices are the same (or rows are not defined), compare by name.
        # Use comparison similar to strcmp()
        # Return -1 if node1.name < node2.name, 1 if node1.name > node2.name, or 0 if they are equal.
        else:
            return (node1.name > node2.name) - (node1.name < node2.name)

    def _sort_children_recursively(self, node: FastEMTreeNode):
        """
        Sort children of a node recursively in the tree control.

        :param node: (FastEMTreeNode) The node whose children are to be sorted.
        """
        item = self._find_item_by_node(node)
        if item:
            # Sort the children of the current item
            self.SortChildren(item)

            # Recursively sort children for each child node
            child, cookie = self.GetFirstChild(item)
            while child and child.IsOk():
                child_node = self.GetPyData(child)
                self._sort_children_recursively(child_node)
                child, cookie = self.GetNextChild(item, cookie)

    def _find_item_by_node(self, node: FastEMTreeNode) -> GenericTreeItem:
        """
        Find the tree item corresponding to a given node.

        :param node: (FastEMTreeNode) The node to find.
        :return: (GenericTreeItem) The tree item corresponding to the node.
        """

        def traverse(item):
            if self.GetPyData(item) == node:
                return item
            child, cookie = self.GetFirstChild(item)
            while child and child.IsOk():
                found = traverse(child)
                if found:
                    return found
                child, cookie = self.GetNextChild(item, cookie)
            return None

        root_item = self.GetRootItem()
        return traverse(root_item)

    def add_widgets_to_item(self, item: GenericTreeItem, node: FastEMTreeNode) -> NodeWindow:
        """
        Add widgets to a tree item based on the associated node.

        :param item: (GenericTreeItem) The tree item to which widgets will be added.
        :param node: (FastEMTreeNode) The node associated with the item.
        :return: (NodeWindow) The NodeWindow instance created for the item.
        """
        # Create a window to hold the widgets
        window = NodeWindow(self, node)

        # Set the window as the item window
        self.SetItemWindow(item, window)

        # Store the reference to the node's window to be later used. For
        # example it can be used during acquisition to access the window's widgets
        self.node_window[node] = window
        return window
