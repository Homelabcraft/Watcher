import os

def replace_in_file(filepath, replacements):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in replacements:
        content = content.replace(old, new)
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

readme_replacements = [
    ("instantly restores the backup", "performs a container rollback to restore the backup"),
    ("deep dependency restarts", "direct dependency restart"),
    ("bit-perfect container recreation", "close configuration parity"),
    ("bit-perfect replicas", "close configuration parity"),
    ("- \"watcher.depends_on=database\" # Restarts the database if web-app is updated.", "- \"watcher.depends_on=database\" # Restarts web-app if database is updated."),
    ("guaranteed crash recovery", "best-effort crash recovery"),
    ("guaranteed atomic container cleanup", ""),
]

changelog_replacements = [
    ("bit-perfect container recreation", "close configuration parity"),
    ("bit-perfect replicas", "close configuration parity"),
    ("instant rollback", "container rollback, not data rollback"),
    ("instantly restores the backup", "performs a container rollback to restore the backup")
]

replace_in_file('README.md', readme_replacements)
replace_in_file('CHANGELOG.md', changelog_replacements)

print("Documentation accuracy replacements done")
