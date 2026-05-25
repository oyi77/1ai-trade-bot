"""
Wallet manager port — defines the contract for on-chain wallet operations.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class IWalletManager(ABC):
    """Abstract interface for wallet management.

    Covers address derivation, balance queries, transaction signing,
    and key import/export.  Adapters may wrap keystore files, hardware
    wallets, or custodial APIs.
    """

    @abstractmethod
    async def get_address(self) -> str:
        """Return the primary wallet address.

        Returns:
            Hex-encoded address string.
        """

    @abstractmethod
    async def get_balance(
        self, token: str = "", chain: str = ""
    ) -> Dict[str, Any]:
        """Query on-chain balance.

        Args:
            token: Token symbol or contract address.
                   Empty string returns native balance.
            chain: Target chain (e.g. "bsc", "ethereum").

        Returns:
            Dict with ``balance``, ``decimals``, ``symbol`` keys.
        """

    @abstractmethod
    async def sign_transaction(
        self, tx_data: Dict[str, Any]
    ) -> str:
        """Sign a transaction and return the raw signed payload.

        Args:
            tx_data: Transaction fields (to, value, data, gas, …).

        Returns:
            Hex-encoded signed transaction.
        """

    @abstractmethod
    async def export_wallet(
        self, password: str
    ) -> Dict[str, Any]:
        """Export (encrypted) wallet credentials.

        Args:
            password: Encryption password for the export.

        Returns:
            Dict containing the encrypted keystore or mnemonic.
        """

    @abstractmethod
    async def import_wallet(
        self, wallet_data: Dict[str, Any], password: str
    ) -> bool:
        """Import a wallet from previously-exported data.

        Args:
            wallet_data: Keystore / mnemonic payload.
            password:    Decryption password.

        Returns:
            True on success.

        Raises:
            ConfigError: on invalid data or wrong password.
        """
