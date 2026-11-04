from collections import Counter
from pathlib import Path
import json
import re

root=Path(__file__).parent/'evidence'
def errors(path):
    lines=path.read_text(encoding='utf-16').splitlines()
    result=Counter()
    for line in lines:
        if line.startswith('[IL]: Error'):
            result[re.sub(r'\]: \[[^\]]+ : ',']: [TARGET : ',line,count=1)]+=1
    return result
before=errors(root/'ilverify-original-full.txt')
after=errors(root/'ilverify-patched-full.txt')
result={'tool':'dotnet-ilverify 9.0.0','selected_methods':137,
        'original_error_count':sum(before.values()),'patched_error_count':sum(after.values()),
        'added_errors':dict(after-before),'removed_errors':dict(before-after),
        'limitation':'Original obfuscated code has verifier errors; differential check is not a clean verification or runtime proof.'}
(root/'ilverify-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='removed_errors'},indent=2))
