# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build/Test Commands
- Run tests: `ansible-playbook -i tests/inventory.ini tests/test_read_config_playbook.yml`
- Run single test: `ansible-playbook -i tests/inventory.ini tests/test_read_config_playbook.yml -t <tag_name>`
- Lint Python code: `flake8 plugins/modules/read_config.py`
- Validate YAML: `yamllint tests/**/*.yml`

## Code Style Guidelines
- **Python**: 4-space indentation, snake_case for functions/variables
- **Docstrings**: Use triple double-quotes (`"""`) with parameter descriptions
- **Imports**: Standard library first, then third-party (including Ansible)
- **Error handling**: Use Ansible's `module.fail_json()` for reporting errors
- **Ansible Documentation**: Maintain `DOCUMENTATION`, `EXAMPLES`, and `RETURN` sections
- **Variable naming**: Use descriptive names that indicate purpose
- **Module structure**: Follow Ansible collection structure conventions