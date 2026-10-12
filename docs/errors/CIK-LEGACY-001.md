# CIK-LEGACY-001

Legacy answers file is unreadable or lacks required keys.

## Cause

.copier-answers.yml cannot be parsed as a YAML mapping, or lacks _src_path, _commit or toolkit_version.

## Fix

```bash
copier recopy --trust --defaults -a .copier-answers.yml
```
Restore the file from git history if it was edited by hand.

## Example

A merge conflict marker left in .copier-answers.yml.
