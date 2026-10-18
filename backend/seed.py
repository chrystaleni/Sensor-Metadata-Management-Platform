"""Create the first System Administrator and optional synthetic demo metadata."""
import argparse
from fastapi.testclient import TestClient
from main import app

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--username',required=True)
parser.add_argument('--password',required=True,help='At least 12 characters; supplied by the operator, never a production default')
parser.add_argument('--display-name',default=None)
parser.add_argument('--organisation',default='OrgX')
parser.add_argument('--department',default='Water Operations')
parser.add_argument('--demo',action='store_true',help='Seed synthetic example records')
args=parser.parse_args()
body={'username':args.username,'password':args.password,'display_name':args.display_name or args.username,'organisation':args.organisation,'department':args.department,'demo':args.demo}
with TestClient(app) as client:
    result=client.post('/api/setup',json=body)
    if not result.is_success: raise SystemExit(f'Bootstrap failed ({result.status_code}): {result.text}')
    print(result.json())
