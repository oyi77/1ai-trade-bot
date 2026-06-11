# Unified Trading Bot

## Overview

The Unified Trading Bot is a multi-brand trading platform that combines the signal processing capabilities of the main trading engine with whitelabel support and unified API integration. This bot enables multiple brands to operate under a single codebase while maintaining complete brand differentiation and customization.

## Architecture

```
unified_bot/
├── api/
│   └── unified_api.py                    # REST API layer
├── core/
│   ├── engine.py                         # Core signal engine
│   ├── quality_gate.py                   # Signal quality filtering
│   └── metrics.py                         # Metrics collection
├── whitelabel/
│   └── __init__.py                       # Whitelabel management
└── main.py                               # Main application entry point
```

## Key Features

### 1. Whitelabel Support
- **Brand Management**: Create and manage multiple brands
- **Feature Toggles**: Enable/disable features per brand
- **Custom Branding**: Brand-specific colors, logos, and themes
- **Domain Isolation**: Separate domains for each brand

### 2. Unified Signal Engine
- **Core Signal Processing**: Based on tradebot architecture
- **Multi-Engine Consensus**: 11+ technical indicators
- **Quality Filtering**: Signal quality assurance
- **Performance Metrics**: Real-time monitoring

### 3. Unified API
- **REST API**: FastAPI-based API layer
- **Signal Generation**: Generate signals for any brand
- **Brand Management**: CRUD operations for brands
- **Metrics**: Real-time metrics and monitoring

### 4. Multi-Brand Architecture
- **Brand Isolation**: Each brand operates independently
- **Feature Gating**: Different features for different brands
- **Pricing Models**: Custom pricing per brand
- **Access Control**: Role-based access control

## Usage

### Quick Start

1. **Initialize the bot**:
   ```bash
   python -m unified_bot
   ```

2. **Register a brand**:
   ```bash
   # TODO: Add brand registration command
   ```

3. **Generate signals**:
   ```bash
   # TODO: Add signal generation command
   ```

4. **Check metrics**:
   ```bash
   # TODO: Add metrics viewing command
   ```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/health` | GET | Detailed health check |
| `/signals/generate` | POST | Generate signal |
| `/signals/queue` | GET | Get signal queue |
| `/brands/{id}` | GET | Get brand info |
| `/brands` | GET | Get all brands |
| `/metrics` | GET | Get all metrics |
| `/metrics/{brand_id}` | GET | Get brand metrics |

## Configuration

### Brand Configuration

```json
{
  "brand_id": "example",
  "domain": "https://example.com",
  "name": "Example Trading Bot",
  "logo_url": "https://example.com/logo.png",
  "primary_color": "#2563eb",
  "features": {
    "enable_real_trading": true,
    "enable_paper_trading": true,
    "max_open_trades": 10
  },
  "pricing": {
    "plans": {
      "free": {"price": 0, "features": ["basic"]},
      "pro": {"price": 99, "features": ["advanced", "priority_support"]}
    }
  }
}
```

### API Configuration

```json
{
  "host": "0.0.0.0",
  "port": 8000,
  "debug": false,
  "workers": 1,
  "timeout": 30,
  "max_request_size": 10485760
}
```

## Development

### Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test
python -m pytest tests/test_engine.py -v
```

### Linting and Formatting

```bash
# Lint code
ruff check unified_bot/ tests/

# Format code
ruff format unified_bot/ tests/

# Type checking
mypy unified_bot/
```

### Architecture Overview

The unified bot architecture combines the strengths of both existing systems:

1. **Signal Processing**: Based on tradebot's 11-engine consensus
2. **User Management**: Enhanced from subscription-bot
3. **Payment Integration**: Unified payment processing
4. **Brand Management**: New whitelabel system

## Benefits

### For Brands
- **Quick Launch**: Launch your brand in minutes
- **Full Features**: Access all trading bot features
- **Custom Branding**: Tailor the interface to your brand
- **Scalable**: Handle multiple users and accounts

### For Developers
- **Single Codebase**: Maintain one codebase
- **Feature Reuse**: Share features across brands
- **Rapid Development**: Faster feature development
- **Simplified Maintenance**: Easier updates and fixes

### For Users
- **Consistent Experience**: Unified interface across brands
- **Rich Features**: Full feature set from both systems
- **Reliable Performance**: Robust signal processing
- **Advanced Analytics**: Comprehensive metrics and reporting

## Migration Guide

### From Existing Bots

1. **Plan Migration**:
   - Assess existing bot features
   - Identify overlaps and gaps
   - Define migration timeline

2. **Code Migration**:
   - Extract common functionality
   - Implement whitelabel system
   - Update signal processing

3. **Data Migration**:
   - Migrate user data
   - Migrate subscription data
   - Migrate configuration data

4. **Testing**:
   - Test all features
   - Validate migration
   - User acceptance testing

### Brand Onboarding

1. **Register Brand**:
   - Create brand configuration
   - Set up branding
   - Configure features and pricing

2. **Configure Features**:
   - Enable/disable features
   - Set up payment methods
   - Configure user access

3. **Launch**:
   - Deploy application
   - Test integration
   - Monitor performance

## Security

### Authentication
- **JWT-based authentication**: Secure token-based authentication
- **Role-based access control**: Different permissions for different roles
- **Rate limiting**: Prevent abuse and ensure fair usage

### Authorization
- **Brand isolation**: Each brand operates independently
- **Feature gating**: Different features for different brands
- **Access control**: Fine-grained control over resources

### Data Protection
- **Encryption**: Secure data transmission and storage
- **Privacy**: Respect user privacy and data protection
- **Compliance**: Meet regulatory requirements

## Future Enhancements

### Planned Features
- **Multi-language support**: Support for multiple languages
- **Advanced analytics**: Enhanced analytics and reporting
- **Mobile integration**: Mobile app integration
- **Webhook support**: Webhook integration for third-party services
- **AI-powered signals**: Integration with AI-powered signal generation

### Performance Improvements
- **Auto-scaling**: Automatic scaling based on demand
- **Load balancing**: Distribute load across multiple servers
- **Caching**: Implement caching for better performance
- **Optimization**: Optimize for high-frequency trading

## License

This project is licensed under the MIT License. See the LICENSE file for more information.

## Support

For support, please contact:
- **GitHub Issues**: Report bugs and feature requests
- **Documentation**: Check the documentation for usage examples
- **Community**: Join the community for discussions

## Contributing

We welcome contributions! Please see the CONTRIBUTING.md file for guidelines on how to contribute to this project.

## Acknowledgments

This project builds upon the following open-source projects:
- **tradebot**: Signal processing engine
- **Subscription-bot**: User management and payment processing
- **FastAPI**: Web framework
- **Pydantic**: Data validation
- **Docker**: Containerization

## Contact

For more information, please visit:
- **GitHub**: https://github.com/your-username/unified-trading-bot
- **Website**: https://your-website.com
- **Documentation**: https://docs.your-website.com