# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

### How to Report

1. **DO NOT** create a public GitHub issue for security vulnerabilities
2. Use GitHub's private vulnerability reporting feature (Security tab > "Report a vulnerability")
3. Or email the maintainers directly with details

### What to Include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Within 30 days for critical issues

## Security Measures

This repository implements the following security measures:

### Automated Security Scanning
- **CodeQL**: Static analysis for code vulnerabilities
- **Dependabot**: Automated dependency vulnerability alerts and updates
- **Secret Scanning**: Detection of accidentally committed secrets
- **TruffleHog**: Additional secret detection in CI/CD

### Best Practices
- All secrets must be stored in environment variables or secret managers
- Dependencies are regularly updated via Dependabot
- Code changes require pull request reviews
- Security alerts are addressed promptly

## Security Checklist for Contributors

Before submitting a PR, ensure:

- [ ] No secrets, API keys, or credentials in the code
- [ ] No hardcoded passwords or tokens
- [ ] Input validation for user-provided data
- [ ] Output encoding to prevent XSS
- [ ] Parameterized queries to prevent SQL injection
- [ ] Dependencies are from trusted sources
- [ ] No sensitive data in logs
