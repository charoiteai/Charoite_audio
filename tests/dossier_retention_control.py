"""Synthetic full-rebuild retention control; args: public source snapshot, SHA label.

No owner configuration or stored data. Generation is a deterministic double.
"""
from pathlib import Path
import contextlib,importlib.util,io,json,sys,tempfile,time
source=Path(sys.argv[1]); label=sys.argv[2]
sys.path[:0]=[str(source/'src'),str(source/'scripts')]
from charoite_graph import dossier
import charoite_paths
spec=importlib.util.spec_from_file_location('nightly_retention_probe',source/'scripts/nightly_dossier.py');nd=importlib.util.module_from_spec(spec);sys.modules[spec.name]=nd;spec.loader.exec_module(nd)
BODY='## Сейчас\nbody-tail\n## Как пришли\n—\n## Решено\n—\n## Открыто\n—\n## Кто в теме\n—'
old_body=BODY.replace('body-tail','old warning ⚠️ [[OldBodyLink]]')
new_body=BODY.replace('body-tail','new warning ⚠️ [[NewBodyLink]]')
results=[]
for revised in (False,True):
 with tempfile.TemporaryDirectory(prefix='charoite818-retention-') as name:
  root=Path(name)/'data';root.mkdir();charoite_paths.use_data_root(root,replace=True);graph=Path(name)/'graph'
  for folder in ('Ядра','Встречи','Досье'):(graph/folder).mkdir(parents=True)
  (graph/'Ядра/Sample.md').write_text('# Sample\nsynthetic core\n')
  for i in range(2):(graph/f'Встречи/Sample_{i}.md').write_text('# Synthetic\n[[Ядра/Sample]] source\n')
  path=graph/'Досье/Sample.md';author='    author code  \n### detail\nauthor-tail  '
  original=dossier.render('Sample',old_body,[],{},'synthetic-fingerprint','2026-11-02').replace('## Правки автора\n\n—\n','## Правки автора\n\n'+author+'\n').replace('## Источники\n','## Источники\n\n- extra source ⚠️ [[OldExtraSource]]\n')
  if revised:original=original.replace('type: досье','type: досье\nревизия: 2026-11-01')
  path.write_text(original);before=path.read_bytes();calls=[]
  def generate(*args,**kwargs):calls.append(1);return new_body
  nd.generate=generate;nd.live_gate.night_window_open=lambda *a,**k:True;nd._graph_snapshot=lambda graph,*a:nd.graphs.mutation_snapshot(graph,nd._root(),0)
  outputs=[]
  for i in range(2):
   with contextlib.redirect_stdout(io.StringIO()):nd.run(graph,{},full=True,dry=False,limit=12)
   outputs.append(path.read_bytes())
   time.sleep(1.05)
  text=outputs[-1].decode();results.append({'revised':revised,'generation_doubles':len(calls),'real_model_calls':0,'old_body_warning_link_retained':'OldBodyLink' in text,'old_extra_source_retained':'OldExtraSource' in text,'new_body_warning_link_retained':'NewBodyLink' in text,'author_retained':author in text,'current_source_links_retained':all(f'Sample_{i}]]' in text for i in range(2)),'stable_after_first':outputs[0]==outputs[1],'whole_original_preserved':outputs[-1]==before})
print(json.dumps({'source':label,'synthetic_only':True,'cases':results},ensure_ascii=False))
