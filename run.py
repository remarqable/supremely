"""Development server entry point."""
from dotenv import load_dotenv

load_dotenv('config/local.env')

# Imported after load_dotenv on purpose: the app package reads config at
# import time, so the .env file has to be in the environment first.
from app import create_app  # noqa: E402

app = create_app()

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 8000))
    reloader = os.environ.get('USE_RELOADER', '1') == '1'
    print(f'\n  Supremely running at http://localhost:{port}\n')
    app.run(host='0.0.0.0', port=port, debug=True, use_reloader=reloader)
