"""Install the copy-editor template into a Type app source directory.

Usage: python3 apply_template.py <app_dir>
Copies App.tsx -> src/App.tsx, app.ts -> convex/app.ts, schema.ts -> convex/schema.ts
and appends editor-styles.css to src/styles.css (once). Run build_page.py afterwards
(or before - order does not matter) to generate src/page/*.
"""
import os, shutil, sys

here = os.path.dirname(os.path.abspath(__file__))
app = sys.argv[1]
shutil.copy(os.path.join(here, 'App.tsx'), os.path.join(app, 'src', 'App.tsx'))
shutil.copy(os.path.join(here, 'app.ts'), os.path.join(app, 'convex', 'app.ts'))
shutil.copy(os.path.join(here, 'schema.ts'), os.path.join(app, 'convex', 'schema.ts'))
styles = os.path.join(app, 'src', 'styles.css')
current = open(styles).read()
if 'Landing page copy editor' not in current:
    open(styles, 'a').write('\n' + open(os.path.join(here, 'editor-styles.css')).read())
print('template applied to', app)
