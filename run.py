"""Development server entry point."""
from dotenv import load_dotenv

load_dotenv('config/local.env')

from app import create_app

app = create_app()

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    reloader = os.environ.get('USE_RELOADER', '1') == '1'
    print(f'\n  Supremely running at http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=reloader)
