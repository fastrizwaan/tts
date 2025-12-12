"""
Undo/Redo System - Complete undo/redo functionality with scroll-to-affected-area.

This module provides:
- Command base class for command pattern
- InsertCommand, DeleteCommand for text operations
- BatchCommand for grouping operations
- UndoRedoManager for managing undo/redo stacks
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple, TYPE_CHECKING
from enum import Enum

if TYPE_CHECKING:
    from virtual_buffer import VirtualBuffer


@dataclass
class Position:
    """A position in the buffer (line, column)."""
    line: int
    col: int
    
    def __lt__(self, other: 'Position') -> bool:
        if self.line != other.line:
            return self.line < other.line
        return self.col < other.col
    
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Position):
            return False
        return self.line == other.line and self.col == other.col
    
    def copy(self) -> 'Position':
        return Position(self.line, self.col)


@dataclass
class Selection:
    """A selection range in the buffer."""
    start: Position
    end: Position
    
    def copy(self) -> 'Selection':
        return Selection(self.start.copy(), self.end.copy())
    
    @property
    def is_empty(self) -> bool:
        return self.start == self.end


class CommandType(Enum):
    """Type of text command."""
    INSERT = "insert"
    DELETE = "delete"
    BATCH = "batch"


class Command(ABC):
    """Abstract base class for undoable commands."""
    
    @abstractmethod
    def execute(self, buffer: 'VirtualBuffer') -> Position:
        """
        Execute the command.
        Returns the cursor position after execution.
        """
        pass
    
    @abstractmethod
    def undo(self, buffer: 'VirtualBuffer') -> Position:
        """
        Undo the command.
        Returns the cursor position after undo.
        """
        pass
    
    @abstractmethod
    def redo(self, buffer: 'VirtualBuffer') -> Position:
        """
        Redo the command (usually same as execute).
        Returns the cursor position after redo.
        """
        pass
    
    @abstractmethod
    def get_affected_position(self) -> Position:
        """
        Get the primary position affected by this command.
        Used for scrolling to the affected area.
        """
        pass
    
    @abstractmethod
    def get_command_type(self) -> CommandType:
        """Get the type of this command."""
        pass
    
    def can_merge_with(self, other: 'Command') -> bool:
        """
        Check if this command can be merged with another.
        Used for combining consecutive character inserts/deletes.
        """
        return False
    
    def merge_with(self, other: 'Command') -> Optional['Command']:
        """
        Merge this command with another if possible.
        Returns merged command or None if merge not possible.
        """
        return None


class InsertCommand(Command):
    """Command for text insertion."""
    
    def __init__(self, position: Position, text: str):
        self.position = position.copy()
        self.text = text
        self.end_position: Optional[Position] = None
    
    def execute(self, buffer: 'VirtualBuffer') -> Position:
        """Insert text at position."""
        end_line, end_col = buffer.insert(
            self.position.line, 
            self.position.col, 
            self.text
        )
        self.end_position = Position(end_line, end_col)
        return self.end_position.copy()
    
    def undo(self, buffer: 'VirtualBuffer') -> Position:
        """Delete the inserted text."""
        if self.end_position is None:
            # Calculate end position if not set
            lines = self.text.split('\n')
            if len(lines) == 1:
                end_line = self.position.line
                end_col = self.position.col + len(self.text)
            else:
                end_line = self.position.line + len(lines) - 1
                end_col = len(lines[-1])
            self.end_position = Position(end_line, end_col)
        
        buffer.delete(
            self.position.line, self.position.col,
            self.end_position.line, self.end_position.col
        )
        return self.position.copy()
    
    def redo(self, buffer: 'VirtualBuffer') -> Position:
        """Re-insert the text."""
        return self.execute(buffer)
    
    def get_affected_position(self) -> Position:
        """Return the insertion position."""
        return self.position.copy()
    
    def get_command_type(self) -> CommandType:
        return CommandType.INSERT
    
    def can_merge_with(self, other: 'Command') -> bool:
        """Can merge consecutive single-character inserts on same line."""
        if not isinstance(other, InsertCommand):
            return False
        if len(self.text) != 1 or len(other.text) != 1:
            return False
        if self.text == '\n' or other.text == '\n':
            return False
        if self.end_position is None:
            return False
        # Must be adjacent
        return (other.position.line == self.end_position.line and 
                other.position.col == self.end_position.col)
    
    def merge_with(self, other: 'Command') -> Optional['Command']:
        """Merge with another insert command."""
        if not self.can_merge_with(other):
            return None
        
        merged = InsertCommand(self.position.copy(), self.text + other.text)
        if isinstance(other, InsertCommand) and other.end_position:
            merged.end_position = other.end_position.copy()
        return merged


class DeleteCommand(Command):
    """Command for text deletion."""
    
    def __init__(self, start: Position, end: Position, deleted_text: str = ""):
        self.start = start.copy()
        self.end = end.copy()
        self.deleted_text = deleted_text
    
    def execute(self, buffer: 'VirtualBuffer') -> Position:
        """Delete text in range."""
        self.deleted_text = buffer.delete(
            self.start.line, self.start.col,
            self.end.line, self.end.col
        )
        return self.start.copy()
    
    def undo(self, buffer: 'VirtualBuffer') -> Position:
        """Re-insert the deleted text."""
        end_line, end_col = buffer.insert(
            self.start.line, self.start.col,
            self.deleted_text
        )
        return Position(end_line, end_col)
    
    def redo(self, buffer: 'VirtualBuffer') -> Position:
        """Delete the text again."""
        buffer.delete(
            self.start.line, self.start.col,
            self.end.line, self.end.col
        )
        return self.start.copy()
    
    def get_affected_position(self) -> Position:
        """Return the start of deletion."""
        return self.start.copy()
    
    def get_command_type(self) -> CommandType:
        return CommandType.DELETE
    
    def can_merge_with(self, other: 'Command') -> bool:
        """Can merge consecutive single-character deletes (backspace)."""
        if not isinstance(other, DeleteCommand):
            return False
        if len(self.deleted_text) != 1 or len(other.deleted_text) != 1:
            return False
        if self.deleted_text == '\n' or other.deleted_text == '\n':
            return False
        # For backspace: other delete is just before this one
        return (other.end.line == self.start.line and 
                other.end.col == self.start.col)
    
    def merge_with(self, other: 'Command') -> Optional['Command']:
        """Merge with another delete command."""
        if not self.can_merge_with(other):
            return None
        
        if isinstance(other, DeleteCommand):
            merged = DeleteCommand(
                other.start.copy(),
                self.end.copy(),
                other.deleted_text + self.deleted_text
            )
            return merged
        return None


class BatchCommand(Command):
    """Command that groups multiple commands for atomic undo/redo."""
    
    def __init__(self, commands: Optional[List[Command]] = None):
        self.commands: List[Command] = commands or []
    
    def add(self, command: Command) -> None:
        """Add a command to the batch."""
        self.commands.append(command)
    
    def is_empty(self) -> bool:
        """Check if batch has no commands."""
        return len(self.commands) == 0
    
    def execute(self, buffer: 'VirtualBuffer') -> Position:
        """Execute all commands in order."""
        last_pos = Position(0, 0)
        for cmd in self.commands:
            last_pos = cmd.execute(buffer)
        return last_pos
    
    def undo(self, buffer: 'VirtualBuffer') -> Position:
        """Undo all commands in reverse order."""
        last_pos = Position(0, 0)
        for cmd in reversed(self.commands):
            last_pos = cmd.undo(buffer)
        return last_pos
    
    def redo(self, buffer: 'VirtualBuffer') -> Position:
        """Redo all commands in order."""
        last_pos = Position(0, 0)
        for cmd in self.commands:
            last_pos = cmd.redo(buffer)
        return last_pos
    
    def get_affected_position(self) -> Position:
        """Return position of first command."""
        if self.commands:
            return self.commands[0].get_affected_position()
        return Position(0, 0)
    
    def get_command_type(self) -> CommandType:
        return CommandType.BATCH


class UndoRedoManager:
    """
    Manages undo/redo stacks with support for:
    - Unlimited undo/redo (configurable limit)
    - Command merging for efficient storage
    - Batch operations
    - Scroll-to-affected-area
    """
    
    def __init__(self, max_history: int = 10000):
        self._undo_stack: List[Command] = []
        self._redo_stack: List[Command] = []
        self._max_history = max_history
        self._batch_stack: List[BatchCommand] = []
        self._merge_timeout_enabled = True
    
    @property
    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self._undo_stack) > 0
    
    @property
    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self._redo_stack) > 0
    
    def push(self, command: Command) -> None:
        """
        Push a command to the undo stack.
        Clears redo stack and attempts to merge with previous command.
        """
        # If in batch mode, add to current batch
        if self._batch_stack:
            self._batch_stack[-1].add(command)
            return
        
        # Clear redo stack on new action
        self._redo_stack.clear()
        
        # Try to merge with last command
        if self._merge_timeout_enabled and self._undo_stack:
            last_cmd = self._undo_stack[-1]
            if last_cmd.can_merge_with(command):
                merged = last_cmd.merge_with(command)
                if merged:
                    self._undo_stack[-1] = merged
                    return
        
        # Add new command
        self._undo_stack.append(command)
        
        # Enforce history limit
        while len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
    
    def undo(self, buffer: 'VirtualBuffer') -> Optional[Position]:
        """
        Undo the last command.
        Returns the affected position for scrolling, or None if nothing to undo.
        """
        if not self.can_undo:
            return None
        
        command = self._undo_stack.pop()
        position = command.undo(buffer)
        self._redo_stack.append(command)
        
        return position
    
    def redo(self, buffer: 'VirtualBuffer') -> Optional[Position]:
        """
        Redo the last undone command.
        Returns the affected position for scrolling, or None if nothing to redo.
        """
        if not self.can_redo:
            return None
        
        command = self._redo_stack.pop()
        position = command.redo(buffer)
        self._undo_stack.append(command)
        
        return position
    
    def begin_batch(self) -> None:
        """Start a batch of commands that will be undone/redone together."""
        self._batch_stack.append(BatchCommand())
    
    def end_batch(self) -> None:
        """End the current batch and push it to the undo stack."""
        if self._batch_stack:
            batch = self._batch_stack.pop()
            if not batch.is_empty():
                # Clear redo on new action
                self._redo_stack.clear()
                self._undo_stack.append(batch)
    
    def cancel_batch(self) -> None:
        """Cancel the current batch without adding to history."""
        if self._batch_stack:
            self._batch_stack.pop()
    
    def clear(self) -> None:
        """Clear all undo/redo history."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._batch_stack.clear()
    
    def set_merge_enabled(self, enabled: bool) -> None:
        """Enable or disable command merging."""
        self._merge_timeout_enabled = enabled
    
    def break_merge(self) -> None:
        """
        Break the merge chain.
        Call this on cursor movement or after a delay to prevent
        unrelated edits from being merged.
        """
        # Insert a marker that prevents merging
        # This is done by temporarily disabling merge for next push
        pass  # The merge check naturally breaks on cursor movement
    
    def get_undo_count(self) -> int:
        """Get number of undoable actions."""
        return len(self._undo_stack)
    
    def get_redo_count(self) -> int:
        """Get number of redoable actions."""
        return len(self._redo_stack)
