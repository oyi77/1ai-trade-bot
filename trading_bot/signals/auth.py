#!/usr/bin/env python3
"""
Firebase authentication and token management
SOLID: Single Responsibility, Dependency Inversion
DRY: Reusable auth components
KISS: Simple token refresh mechanism
"""

from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod
from pathlib import Path
import requests
import json
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class AndroidDevice:
    """Android device configuration (Single Responsibility)"""
    user_agent: str = 'Dalvik/2.1.0 (Linux; U; Android 11; sdk_gphone_arm64 Build/RSR1.240422.006)'
    package_name: str = 'com.zyncas.signals'
    cert_sha1: str = 'B0D23E091BABBAE805AD4D63D6831591F55D40B2'
    
    def get_headers(self) -> Dict[str, str]:
        """Get Android HTTP headers"""
        return {
            'User-Agent': self.user_agent,
            'X-Android-Package': self.package_name,
            'X-Android-Cert': self.cert_sha1,
            'Connection': 'Keep-Alive',
            'Accept-Encoding': 'gzip',
            'Accept-Language': 'en-, en-US',
            'X-Client-Version': 'Android/Fallback/X22001002/FirebaseCore-Android',
            'X-Firebase-GMPID': '1:727577331880:android:70c91e8ca6537867d8da9d',
            'X-Firebase-Client': 'H4sIAAAAAAAAAKtWykhNLCpJSk0sKVayio7VUSpLLSrOzM9TslIyUqoFAFyivEQfAAAA',
            'X-Firebase-AppCheck': 'eyJlcnJvciI6IlVOS05PV05fRVJST1IifQ==',
        }


@dataclass
class TokenData:
    """Token data structure"""
    access_token: str
    id_token: str
    refresh_token: str
    expires_at: float
    user_id: Optional[str] = None
    project_id: Optional[str] = None
    
    def is_valid(self, buffer_seconds: int = 300) -> bool:
        """Check if token is still valid (with buffer)"""
        return time.time() < (self.expires_at - buffer_seconds)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary"""
        return asdict(self)


class ITokenStorage(ABC):
    """Interface for token storage (Dependency Inversion)"""
    
    @abstractmethod
    def save(self, token: TokenData) -> None:
        """Save token"""
        pass
    
    @abstractmethod
    def load(self) -> Optional[TokenData]:
        """Load token"""
        pass
    
    @abstractmethod
    def exists(self) -> bool:
        """Check if token exists"""
        pass


class JsonTokenStorage(ITokenStorage):
    """JSON file-based token storage"""
    
    def __init__(self, filepath: str = ".firebase_tokens.json"):
        self.filepath = Path(filepath)
    
    def save(self, token: TokenData) -> None:
        """Save token to JSON file"""
        try:
            self.filepath.write_text(json.dumps(token.to_dict(), indent=2))
            logger.info(f"✅ Token saved to {self.filepath}")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")
    
    def load(self) -> Optional[TokenData]:
        """Load token from JSON file"""
        try:
            if not self.filepath.exists():
                return None
            data = json.loads(self.filepath.read_text())
            return TokenData(**data)
        except Exception as e:
            logger.error(f"Failed to load token: {e}")
            return None
    
    def exists(self) -> bool:
        """Check if token file exists"""
        return self.filepath.exists()


class FirebaseTokenRefresher:
    """Firebase token management with auto-refresh"""
    
    def __init__(
        self,
        api_key: str,
        refresh_token: Optional[str] = None,
        storage: Optional[ITokenStorage] = None,
        device: Optional[AndroidDevice] = None
    ):
        self.api_key = api_key
        self.storage = storage or JsonTokenStorage()
        self.device = device or AndroidDevice()
        self.token: Optional[TokenData] = None
        
        if refresh_token:
            self._create_token_from_refresh(refresh_token)
        else:
            self._load_from_storage()
    
    def _create_token_from_refresh(self, refresh_token: str) -> None:
        """Create minimal token data from refresh token"""
        self.token = TokenData(
            access_token="",
            id_token="",
            refresh_token=refresh_token,
            expires_at=0
        )
    
    def _load_from_storage(self) -> None:
        """Load token from storage"""
        self.token = self.storage.load()
        if self.token:
            logger.info("✅ Loaded token from storage")
    
    def refresh(self) -> bool:
        """Refresh token using Firebase API"""
        if not self.token or not self.token.refresh_token:
            logger.error("No refresh token available")
            return False
        
        try:
            url = f"https://securetoken.googleapis.com/v1/token?key={self.api_key}"
            
            headers = {
                **self.device.get_headers(),
                'Content-Type': 'application/json'
            }
            
            payload = {
                "grantType": "refresh_token",
                "refreshToken": self.token.refresh_token
            }
            
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                
                expires_in = int(data.get('expires_in', 3600))
                self.token = TokenData(
                    access_token=data['access_token'],
                    id_token=data['id_token'],
                    refresh_token=data['refresh_token'],
                    expires_at=time.time() + expires_in,
                    user_id=data.get('user_id'),
                    project_id=data.get('project_id')
                )
                
                self.storage.save(self.token)
                logger.info(f"✅ Token refreshed (expires in {expires_in}s)")
                return True
            else:
                logger.error(f"❌ Refresh failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Refresh error: {e}")
            return False
    
    def get_valid_token(self) -> Optional[TokenData]:
        """Get valid token, auto-refresh if needed"""
        if not self.token:
            return None
        
        if not self.token.is_valid():
            logger.info("Token expired, refreshing...")
            if not self.refresh():
                return None
        
        return self.token
    
    def get_auth_header(self) -> Optional[str]:
        """Get Authorization header value"""
        token = self.get_valid_token()
        return f"Bearer {token.id_token}" if token else None
    
    def set_refresh_token(self, refresh_token: str) -> bool:
        """Set new refresh token and refresh immediately"""
        self._create_token_from_refresh(refresh_token)
        return self.refresh()


class FirebaseClient:
    """High-level Firebase client (Open/Closed Principle)"""
    
    def __init__(
        self,
        api_key: str,
        project_id: str,
        refresh_token: Optional[str] = None,
        storage: Optional[ITokenStorage] = None,
        device: Optional[AndroidDevice] = None
    ):
        self.api_key = api_key
        self.project_id = project_id
        self.refresher = FirebaseTokenRefresher(api_key, refresh_token, storage, device)
        self.base_url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents"
    
    def get_collection(self, collection: str, page_size: int = 100, page_token: Optional[str] = None) -> Optional[Dict]:
        """
        Get Firestore collection documents with pagination support
        
        Returns:
            Dict with 'documents' and 'nextPageToken' if available
        """
        token = self.refresher.get_valid_token()
        if not token:
            logger.error("Failed to get valid token")
            return None
        
        try:
            url = f"{self.base_url}/{collection}"
            
            headers = {
                **self.refresher.device.get_headers(),
                'Authorization': f'Bearer {token.id_token}',
                'Content-Type': 'application/json'
            }
            
            params = {'pageSize': page_size}
            if page_token:
                params['pageToken'] = page_token
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                documents = data.get('documents', [])
                next_token = data.get('nextPageToken')
                
                logger.info(f"✅ Retrieved {len(documents)} documents from {collection}")
                
                return {
                    'documents': documents,
                    'nextPageToken': next_token
                }
            else:
                logger.error(f"❌ Failed to get {collection}: {response.status_code}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            return None
    
    def get_all_documents(self, collection: str, page_size: int = 300, max_pages: Optional[int] = None) -> List[Dict]:
        """
        Fetch ALL documents from a collection with pagination
        
        Args:
            collection: Collection name
            page_size: Documents per page (max 300 for Firestore)
            max_pages: Maximum pages to fetch (None = unlimited)
        
        Returns:
            List of all documents
        """
        all_documents = []
        page_token = None
        page_count = 0
        
        logger.info(f"🔄 Fetching ALL documents from {collection}...")
        
        while True:
            if max_pages and page_count >= max_pages:
                logger.info(f"⚠️  Reached max pages limit ({max_pages})")
                break
            
            result = self.get_collection(collection, page_size, page_token)
            
            if not result:
                break
            
            documents = result.get('documents', [])
            next_token = result.get('nextPageToken')
            
            if documents:
                all_documents.extend(documents)
                page_count += 1
                logger.info(f"📄 Page {page_count}: {len(documents)} docs (Total: {len(all_documents)})")
            
            # If no next token, we've reached the end
            if not next_token:
                break
            
            page_token = next_token
        
        logger.info(f"✅ Fetched {len(all_documents)} total documents from {collection}")
        return all_documents
    
    def parse_document(self, doc: Dict) -> Dict:
        """Parse Firestore document format to simple dict (DRY)"""
        fields = doc.get('fields', {})
        parsed = {}
        
        type_mapping = {
            'stringValue': str,
            'integerValue': int,
            'doubleValue': float,
            'booleanValue': bool,
            'timestampValue': str,
            'nullValue': lambda x: None
        }
        
        for key, value in fields.items():
            for type_key, converter in type_mapping.items():
                if type_key in value:
                    parsed[key] = converter(value[type_key]) if callable(converter) and type_key != 'nullValue' else value[type_key]
                    break
            else:
                parsed[key] = value
        
        parsed['_id'] = doc.get('name', '').split('/')[-1]
        return parsed
    
    def get_notifications(self, page_size: int = 50, page_token: Optional[str] = None) -> Optional[Dict]:
        """Get notifications collection (single page)"""
        return self.get_collection('notifications', page_size, page_token)
    
    def get_all_notifications(self, page_size: int = 300, max_pages: Optional[int] = None) -> List[Dict]:
        """Fetch ALL notifications from Firebase"""
        return self.get_all_documents('notifications', page_size, max_pages)
    
    def get_all_signals(self, collection: str = 'signals', page_size: int = 300, max_pages: Optional[int] = None) -> List[Dict]:
        """Fetch ALL signals from Firebase (spot or futures)"""
        return self.get_all_documents(collection, page_size, max_pages)

