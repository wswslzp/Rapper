"""
Simple authentication module for rapper.

Provides basic user authentication functionality with password hashing
and validation capabilities.
"""

import hashlib
import secrets
import time
from typing import Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class User:
    """Represents a user with authentication credentials."""
    username: str
    password_hash: str
    salt: str
    created_at: float
    last_login: Optional[float] = None
    is_active: bool = True


class SimpleAuth:
    """Simple authentication system with in-memory user storage."""

    def __init__(self):
        self.users: Dict[str, User] = {}
        self.sessions: Dict[str, Tuple[str, float]] = {}  # session_id -> (username, expiry)
        self.session_timeout = 3600  # 1 hour in seconds

    def _generate_salt(self) -> str:
        """Generate a random salt for password hashing."""
        return secrets.token_hex(16)

    def _hash_password(self, password: str, salt: str) -> str:
        """Hash a password with the given salt using SHA-256."""
        return hashlib.sha256((password + salt).encode()).hexdigest()

    def create_user(self, username: str, password: str) -> bool:
        """
        Create a new user account.

        Args:
            username: The username for the new account
            password: The plain text password

        Returns:
            True if user was created successfully, False if username already exists
        """
        if username in self.users:
            return False

        salt = self._generate_salt()
        password_hash = self._hash_password(password, salt)

        user = User(
            username=username,
            password_hash=password_hash,
            salt=salt,
            created_at=time.time()
        )

        self.users[username] = user
        return True

    def authenticate(self, username: str, password: str) -> bool:
        """
        Authenticate a user with username and password.

        Args:
            username: The username to authenticate
            password: The plain text password

        Returns:
            True if authentication successful, False otherwise
        """
        if username not in self.users:
            return False

        user = self.users[username]
        if not user.is_active:
            return False

        password_hash = self._hash_password(password, user.salt)

        if password_hash == user.password_hash:
            user.last_login = time.time()
            return True

        return False

    def create_session(self, username: str) -> Optional[str]:
        """
        Create a session token for an authenticated user.

        Args:
            username: The username to create a session for

        Returns:
            Session token string if successful, None otherwise
        """
        if username not in self.users:
            return None

        session_id = secrets.token_urlsafe(32)
        expiry_time = time.time() + self.session_timeout

        self.sessions[session_id] = (username, expiry_time)
        return session_id

    def validate_session(self, session_id: str) -> Optional[str]:
        """
        Validate a session token and return the username if valid.

        Args:
            session_id: The session token to validate

        Returns:
            Username if session is valid, None otherwise
        """
        if session_id not in self.sessions:
            return None

        username, expiry_time = self.sessions[session_id]

        if time.time() > expiry_time:
            # Session expired, remove it
            del self.sessions[session_id]
            return None

        return username

    def logout(self, session_id: str) -> bool:
        """
        Logout a user by invalidating their session.

        Args:
            session_id: The session token to invalidate

        Returns:
            True if session was found and invalidated, False otherwise
        """
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False

    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """
        Change a user's password after verifying the old password.

        Args:
            username: The username whose password to change
            old_password: The current password for verification
            new_password: The new password to set

        Returns:
            True if password was changed successfully, False otherwise
        """
        if not self.authenticate(username, old_password):
            return False

        user = self.users[username]
        salt = self._generate_salt()
        password_hash = self._hash_password(new_password, salt)

        user.salt = salt
        user.password_hash = password_hash

        return True

    def deactivate_user(self, username: str) -> bool:
        """
        Deactivate a user account (soft delete).

        Args:
            username: The username to deactivate

        Returns:
            True if user was deactivated, False if user not found
        """
        if username not in self.users:
            return False

        self.users[username].is_active = False

        # Invalidate all sessions for this user
        sessions_to_remove = [
            session_id for session_id, (user, _) in self.sessions.items()
            if user == username
        ]
        for session_id in sessions_to_remove:
            del self.sessions[session_id]

        return True

    def cleanup_expired_sessions(self) -> int:
        """
        Remove all expired sessions.

        Returns:
            Number of sessions that were removed
        """
        current_time = time.time()
        expired_sessions = [
            session_id for session_id, (_, expiry) in self.sessions.items()
            if current_time > expiry
        ]

        for session_id in expired_sessions:
            del self.sessions[session_id]

        return len(expired_sessions)


# Global auth instance for easy access
auth = SimpleAuth()


# Convenience functions for common operations
def login(username: str, password: str) -> Optional[str]:
    """
    Login a user and return a session token.

    Returns:
        Session token if successful, None otherwise
    """
    if auth.authenticate(username, password):
        return auth.create_session(username)
    return None


def register(username: str, password: str) -> bool:
    """
    Register a new user account.

    Returns:
        True if registration successful, False if username taken
    """
    return auth.create_user(username, password)


def check_session(session_id: str) -> Optional[str]:
    """
    Check if a session is valid and return the username.

    Returns:
        Username if session valid, None otherwise
    """
    return auth.validate_session(session_id)


def require_auth(session_id: str) -> Tuple[bool, Optional[str]]:
    """
    Check if a session is valid and return auth status with username.

    Returns:
        Tuple of (is_authenticated, username)
    """
    username = auth.validate_session(session_id)
    return (username is not None, username)


if __name__ == "__main__":
    # Simple demonstration of the auth system
    print("Simple Auth System Demo")
    print("-" * 30)

    # Register a test user
    if register("admin", "password123"):
        print("✓ User 'admin' registered successfully")

    # Try to login
    session_token = login("admin", "password123")
    if session_token:
        print(f"✓ Login successful, session token: {session_token[:16]}...")

    # Validate session
    username = check_session(session_token)
    if username:
        print(f"✓ Session valid for user: {username}")

    # Logout
    if auth.logout(session_token):
        print("✓ Logout successful")

    # Try to use invalidated session
    username = check_session(session_token)
    if not username:
        print("✓ Session invalidated after logout")

    print("\nDemo completed successfully!")