#!/usr/bin/env python3
"""
Token Management CLI - Command-line interface for token operations
"""

from .auth import FirebaseClient, JsonTokenStorage
import argparse
import logging
import sys

logger = logging.getLogger(__name__)


class TokenCLI:
    """CLI for managing Firebase tokens"""
    
    def __init__(self, api_key: str, project_id: str):
        self.api_key = api_key
        self.project_id = project_id
    
    def cmd_set_token(self, refresh_token: str) -> int:
        """Set refresh token from intercepted request"""
        client = FirebaseClient(self.api_key, self.project_id, refresh_token=refresh_token)
        
        print("\n🔄 Refreshing token...")
        if client.refresher.refresh():
            print("✅ Token set and refreshed successfully!")
            print(f"💾 Saved to: {client.refresher.storage.filepath}")
            print("\n💡 You can now use this token indefinitely!")
            return 0
        else:
            print("❌ Failed to refresh token")
            return 1
    
    def cmd_refresh(self) -> int:
        """Manually refresh existing token"""
        client = FirebaseClient(self.api_key, self.project_id)
        
        if not client.refresher.token:
            print("❌ No token found!")
            print("💡 First set a token: python -m trading_bot.utils.cli set <refresh_token>")
            return 1
        
        print("\n🔄 Refreshing token...")
        if client.refresher.refresh():
            print("✅ Token refreshed successfully!")
            return 0
        else:
            print("❌ Refresh failed")
            return 1
    
    def cmd_status(self) -> int:
        """Show token status"""
        client = FirebaseClient(self.api_key, self.project_id)
        
        if not client.refresher.token:
            print("❌ No token found!")
            print("💡 First set a token: python -m trading_bot.utils.cli set <refresh_token>")
            return 1
        
        token = client.refresher.token
        
        print("\n📊 Token Status")
        print("=" * 60)
        print(f"User ID: {token.user_id or 'N/A'}")
        print(f"Project ID: {token.project_id or 'N/A'}")
        
        if token.is_valid():
            import time
            remaining = int(token.expires_at - time.time())
            mins = remaining // 60
            secs = remaining % 60
            print(f"Status: ✅ Valid ({mins}m {secs}s remaining)")
        else:
            print("Status: ⚠️  Expired (will auto-refresh on next use)")
        
        print(f"Storage: {client.refresher.storage.filepath}")
        print("=" * 60)
        return 0
    
    def cmd_test(self, collection: str = 'signals', limit: int = 5) -> int:
        """Test token by fetching data"""
        client = FirebaseClient(self.api_key, self.project_id)
        
        if not client.refresher.token:
            print("❌ No token found!")
            print("💡 First set a token: python -m trading_bot.utils.cli set <refresh_token>")
            return 1
        
        print("\n🧪 Testing token...")
        print(f"📡 Fetching {collection} collection...")
        
        docs = client.get_collection(collection, page_size=limit)
        
        if docs:
            print(f"\n✅ Success! Retrieved {len(docs)} documents")
            print("\n📄 Sample data:")
            print("-" * 60)
            
            for i, doc in enumerate(docs[:3], 1):
                parsed = client.parse_document(doc)
                print(f"\n{i}. ID: {parsed.get('_id', 'N/A')}")
                for key, value in list(parsed.items())[:5]:
                    if key != '_id':
                        print(f"   {key}: {value}")
            
            print("\n" + "-" * 60)
            return 0
        else:
            print("❌ Failed to fetch data")
            return 1


def main():
    """CLI entry point"""
    # Firebase config
    API_KEY = "AIzaSyBmmH9F51pdgm3hxH8On_wGb9WMkvn8EKs"
    PROJECT_ID = "signals-61284"
    
    parser = argparse.ArgumentParser(
        description='Firebase Token Manager - No Emulator Needed!',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Set refresh token
  python -m trading_bot.utils.cli set 'AMf-vBxVNSbt7b...'
  
  # Check token status
  python -m trading_bot.utils.cli status
  
  # Manually refresh token
  python -m trading_bot.utils.cli refresh
  
  # Test token by fetching data
  python -m trading_bot.utils.cli test signals
  python -m trading_bot.utils.cli test futures --limit 10
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # Set token command
    set_parser = subparsers.add_parser('set', help='Set refresh token')
    set_parser.add_argument('refresh_token', help='Refresh token from intercepted request')
    
    # Refresh command
    subparsers.add_parser('refresh', help='Manually refresh token')
    
    # Status command
    subparsers.add_parser('status', help='Show token status')
    
    # Test command
    test_parser = subparsers.add_parser('test', help='Test token by fetching data')
    test_parser.add_argument('collection', nargs='?', default='signals', 
                            help='Collection to fetch (default: signals)')
    test_parser.add_argument('--limit', type=int, default=5, 
                            help='Number of documents to fetch (default: 5)')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    # Initialize CLI
    cli = TokenCLI(API_KEY, PROJECT_ID)
    
    # Execute command
    if args.command == 'set':
        return cli.cmd_set_token(args.refresh_token)
    elif args.command == 'refresh':
        return cli.cmd_refresh()
    elif args.command == 'status':
        return cli.cmd_status()
    elif args.command == 'test':
        return cli.cmd_test(args.collection, args.limit)
    
    return 1


if __name__ == "__main__":
    sys.exit(main())

