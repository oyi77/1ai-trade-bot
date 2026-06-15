"""Whitelabel management: brand configs, feature toggles, pricing, branding."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

LOG = logging.getLogger(__name__)
class BrandStatus(Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    SUSPENDED = "suspended"
    PENDING = "pending"
class FeatureCategory(str, Enum):
    CORE_TRADING = "core_trading"
    SIGNAL_GENERATION = "signal_generation"
    PAYMENT_PROCESSING = "payment_processing"
    USER_MANAGEMENT = "user_management"
    ANALYTICS = "analytics"
    RISK_MANAGEMENT = "risk_management"
    NOTIFICATIONS = "notifications"
    WEBHOOKS = "webhooks"
    MOBILE_ACCESS = "mobile_access"
class PlanType(str, Enum):
    FREE = "free"
    BASIC = "basic"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"
class PaymentMethod(str, Enum):
    SCALEV = "scalev"
    STRIPE = "stripe"
    PAYPAL = "paypal"
    BANK_TRANSFER = "bank_transfer"
    E_WALLET = "e_wallet"
    CRYPTO = "crypto"
class Currency(str, Enum):
    USD = "USD"
    IDR = "IDR"
    EUR = "EUR"
    GBP = "GBP"
    BTC = "BTC"
    ETH = "ETH"
class BrandFeatures(BaseModel):
    """Feature configuration for a brand."""
    
    # Core Trading Features
    enable_real_trading: bool = False
    enable_paper_trading: bool = True
    max_open_trades: int = 10
    risk_percentage: float = 0.02
    
    # Signal Features
    enable_signal_generation: bool = True
    min_confidence_threshold: float = 0.7
    signal_providers: List[str] = Field(default_factory=list)
    custom_indicators: List[str] = Field(default_factory=list)
    
    # Payment Features
    payment_methods: List[PaymentMethod] = Field(default_factory=list)
    auto_subscription: bool = True
    trial_enabled: bool = True
    trial_days: int = 7
    
    # User Management
    email_verification_required: bool = True
    phone_verification_required: bool = False
    two_factor_auth_required: bool = False
    kyc_verification_required: bool = False
    
    # Analytics
    enable_analytics: bool = True
    analytics_retention_days: int = 90
    custom_reports_enabled: bool = False
    
    # Risk Management
    daily_loss_limit: float = 0.05
    position_size_limit: float = 0.1
    max_drawdown_limit: float = 0.2
    
    # Notifications
    email_notifications: bool = True
    telegram_notifications: bool = False
    push_notifications: bool = True
    
    # Webhooks
    webhook_endpoints: List[str] = Field(default_factory=list)
    webhook_events: List[str] = Field(default_factory=list)
    
    # Mobile
    mobile_app_enabled: bool = True
    mobile_app_name: str = "Trading Bot"
    
    # Custom Settings
    custom_settings: Dict[str, Any] = Field(default_factory=dict)
class BrandPricing(BaseModel):
    """Pricing configuration for a brand."""
    
    # Subscription Plans
    plans: Dict[PlanType, Dict[str, Any]] = Field(default_factory=dict)
    
    # One-time Payments
    one_time_payments: Dict[str, float] = Field(default_factory=dict)
    
    # Features Included
    features_included: Dict[str, List[str]] = Field(default_factory=dict)
    
    # Currency
    default_currency: Currency = Currency.USD
    
    # Payment Gateways
    payment_gateways: Dict[PaymentMethod, Dict[str, Any]] = Field(default_factory=dict)
    
    # Discounts
    welcome_discount_enabled: bool = True
    welcome_discount_percentage: float = 0.0
    referral_discount_enabled: bool = True
    referral_discount_percentage: float = 0.1
class BrandBranding(BaseModel):
    """Branding configuration for a brand."""
    
    # Basic Branding
    brand_name: str = ""
    domain: str = ""
    logo_url: Optional[str] = None
    logo_dark_url: Optional[str] = None
    favicon_url: Optional[str] = None
    
    # Colors
    primary_color: str = "#2563eb"
    secondary_color: str = "#64748b"
    accent_color: str = "#10b981"
    background_color: str = "#ffffff"
    text_color: str = "#1f2937"
    
    # Typography
    heading_font: str = "Inter, sans-serif"
    body_font: str = "Inter, sans-serif"
    mono_font: str = "JetBrains Mono, monospace"
    
    # Images
    hero_image_url: Optional[str] = None
    feature_images: List[str] = Field(default_factory=list)
    testimonial_images: List[str] = Field(default_factory=list)
    
    # Custom CSS/JS
    custom_css: Optional[str] = None
    custom_js: Optional[str] = None
    custom_html: Optional[str] = None
    
    # SEO
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    meta_keywords: Optional[str] = None
    og_image_url: Optional[str] = None
    twitter_image_url: Optional[str] = None
class BrandConfiguration(BaseModel):
    """Brand identity, features, pricing, branding, and rate limits."""
    
    # Basic Information
    brand_id: str = Field(..., description="Unique brand identifier")
    status: BrandStatus = Field(default=BrandStatus.PENDING)
    
    # Branding
    branding: BrandBranding = Field(default_factory=BrandBranding)
    
    # Features
    features: BrandFeatures = Field(default_factory=BrandFeatures)
    
    # Pricing
    pricing: BrandPricing = Field(default_factory=BrandPricing)
    
    # Technical Settings
    domain: str = Field(..., description="Brand domain or subdomain")
    language: str = Field(default="en", description="Default language")
    timezone: str = Field(default="UTC", description="Timezone")
    currency: Currency = Field(default=Currency.USD)
    
    # Integration Settings
    payment_providers: List[PaymentMethod] = Field(default_factory=list)
    signal_providers: List[str] = Field(default_factory=list)
    webhook_endpoints: List[str] = Field(default_factory=list)
    
    # Rate Limits
    rate_limits: Dict[str, int] = Field(default_factory=lambda: {
        "api_requests_per_minute": 60,
        "signal_generation_per_minute": 10,
        "payment_processing_per_minute": 20,
        "user_registrations_per_hour": 5,
    })
    
    # Time-based Settings
    maintenance_windows: List[Dict[str, str]] = Field(default_factory=list)
    feature_flags: Dict[str, bool] = Field(default_factory=dict)
    
    # Metadata
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    created_by: str = Field(default="system")
    
    # Statistics
    user_count: int = 0
    active_subscriptions: int = 0
    total_revenue: float = 0.0
    
    # Custom Configuration
    custom_config: Dict[str, Any] = Field(default_factory=dict)
    
    @field_validator("brand_id", "domain")
    @classmethod
    def validate_required_fields(cls, v):
        if not v or not str(v).strip():
            raise ValueError("brand_id and domain cannot be empty")
        return v.strip()

    @field_validator("brand_id")
    @classmethod
    def validate_brand_id_format(cls, v):
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("brand_id must be alphanumeric with underscores or hyphens")
        return v.lower()

    @field_validator("domain")
    @classmethod
    def validate_domain_format(cls, v):
        if not v.startswith("http://") and not v.startswith("https://"):
            v = "https://" + v
        return v
    
    def is_feature_enabled(self, feature_category: FeatureCategory, feature_name: str) -> bool:
        """Check if a specific feature is enabled for this brand."""
        # Implement feature toggle logic here
        return True
    
    def get_feature_config(self, feature_category: FeatureCategory) -> Dict[str, Any]:
        """Get feature configuration for a category."""
        # Return feature configuration based on brand settings
        return {}
    
    def can_user_access_feature(self, user_id: str, feature_category: FeatureCategory, feature_name: str) -> bool:
        """Check if a user can access a specific feature based on their subscription."""
        # Implement access control logic here
        return True
    
    def get_pricing_for_plan(self, plan_type: PlanType) -> Dict[str, Any]:
        """Get pricing information for a specific plan."""
        if plan_type not in self.pricing.plans:
            return {}
        return self.pricing.plans[plan_type].copy()
    
    def is_domain_allowed(self, domain: str) -> bool:
        """Check if a domain is allowed for this brand."""
        # Implement domain validation logic
        return self.domain in [domain, f"www.{self.domain}", f"api.{self.domain}"]
    
    def update_feature(self, feature_category: FeatureCategory, feature_name: str, value: Any) -> None:
        """Update a feature configuration."""
        # Implement feature update logic
        pass
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for storage."""
        return json.loads(self.model_dump_json())
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrandConfiguration":
        """Create configuration from dictionary."""
        # Handle datetime conversion
        if "created_at" in data and isinstance(data["created_at"], str):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if "updated_at" in data and isinstance(data["updated_at"], str):
            data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        
        # Handle enum conversions
        if "status" in data and isinstance(data["status"], str):
            data["status"] = BrandStatus(data["status"])
        if "currency" in data and isinstance(data["currency"], str):
            data["currency"] = Currency(data["currency"])
        
        # Handle nested objects
        if "branding" in data and isinstance(data["branding"], dict):
            data["branding"] = BrandBranding(**data["branding"])
        if "features" in data and isinstance(data["features"], dict):
            data["features"] = BrandFeatures(**data["features"])
        if "pricing" in data and isinstance(data["pricing"], dict):
            data["pricing"] = BrandPricing(**data["pricing"])
        
        return cls(**data)
class BrandManager:
    """CRUD for brand configurations, backed by a JSON file."""
    
    def __init__(self, config_file: Optional[str] = None):
        self.brands: Dict[str, BrandConfiguration] = {}
        self.config_file = config_file or "brands.json"
        self.load_brands()
    
    def register_brand(self, config: BrandConfiguration) -> bool:
        """
        Register a new brand.
        
        Args:
            config: Brand configuration
            
        Returns:
            True if registration successful, False otherwise
        """
        try:
            if config.brand_id in self.brands:
                LOG.warning("Brand %s already exists", config.brand_id)
                return False
            
            self.brands[config.brand_id] = config
            self.save_brands()
            LOG.info("Brand %s registered successfully", config.brand_id)
            return True
            
        except Exception as e:
            LOG.error("Failed to register brand %s: %s", config.brand_id, e)
            return False
    
    def get_brand(self, brand_id: str) -> Optional[BrandConfiguration]:
        """
        Get brand configuration.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            Brand configuration or None if not found
        """
        return self.brands.get(brand_id)
    
    def update_brand(self, brand_id: str, config: BrandConfiguration) -> bool:
        """
        Update brand configuration.
        
        Args:
            brand_id: Brand identifier
            config: New brand configuration
            
        Returns:
            True if update successful, False otherwise
        """
        try:
            if brand_id not in self.brands:
                LOG.warning("Brand %s not found", brand_id)
                return False
            
            self.brands[brand_id] = config
            config.updated_at = datetime.now()
            self.save_brands()
            LOG.info("Brand %s updated successfully", brand_id)
            return True
            
        except Exception as e:
            LOG.error("Failed to update brand %s: %s", brand_id, e)
            return False
    
    def delete_brand(self, brand_id: str) -> bool:
        """
        Delete brand.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            True if deletion successful, False otherwise
        """
        try:
            if brand_id not in self.brands:
                LOG.warning("Brand %s not found", brand_id)
                return False
            
            del self.brands[brand_id]
            self.save_brands()
            LOG.info("Brand %s deleted successfully", brand_id)
            return True
            
        except Exception as e:
            LOG.error("Failed to delete brand %s: %s", brand_id, e)
            return False
    
    def get_all_brands(self) -> List[BrandConfiguration]:
        """
        Get all brand configurations.
        
        Returns:
            List of brand configurations
        """
        return list(self.brands.values())
    
    def is_brand_active(self, brand_id: str) -> bool:
        """
        Check if brand is active.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            True if brand is active, False otherwise
        """
        brand = self.get_brand(brand_id)
        return brand is not None and brand.status == BrandStatus.ACTIVE
    
    def get_brand_stats(self, brand_id: str) -> Dict[str, Any]:
        """
        Get brand statistics.
        
        Args:
            brand_id: Brand identifier
            
        Returns:
            Brand statistics
        """
        brand = self.get_brand(brand_id)
        if not brand:
            return {}
        
        return {
            "brand_id": brand.brand_id,
            "status": brand.status.value,
            "domain": brand.domain,
            "user_count": brand.user_count,
            "active_subscriptions": brand.active_subscriptions,
            "total_revenue": brand.total_revenue,
            "created_at": brand.created_at.isoformat(),
            "updated_at": brand.updated_at.isoformat(),
        }
    
    def load_brands(self) -> None:
        """
        Load brands from configuration file.
        """
        try:
            if Path(self.config_file).exists():
                with open(self.config_file, "r") as f:
                    data = json.load(f)
                
                for brand_data in data.get("brands", []):
                    brand = BrandConfiguration.from_dict(brand_data)
                    self.brands[brand.brand_id] = brand
                
                LOG.info("Loaded %d brands from %s", len(self.brands), self.config_file)
            else:
                LOG.info("No brand configuration file found: %s", self.config_file)
                
        except Exception as e:
            LOG.error("Failed to load brands: %s", e)
    
    def save_brands(self) -> None:
        """
        Save brands to configuration file.
        """
        try:
            data = {
                "brands": [brand.to_dict() for brand in self.brands.values()],
                "last_updated": datetime.now().isoformat(),
            }
            
            with open(self.config_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
                
            LOG.info("Saved %d brands to %s", len(self.brands), self.config_file)
            
        except Exception as e:
            LOG.error("Failed to save brands: %s", e)
