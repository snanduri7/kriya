// A scriptable stand-in for `kriya kup-stdio`, used ONLY by kriya_service.test.ts. Behaviour is chosen by FAKE_SERVICE (comma list):
//   ready=skip|garbage|late   what the first line is (default: the real ready event)
//   die-after=N               exit(3) after answering N requests
//   die-on=<operation>        exit(3) WITHOUT answering that operation
//   hang-on=<operation>       never answer that operation
//   wrong-id                  answer with an id that is not in flight
//   garbage-on=<operation>    answer that operation with a non-JSON line
//   big-on=<operation>        answer with a line over the limit
//   refuse-on=<operation>     answer with a typed refusal {ok:false}
//   noenvelope-on=<operation> answer {ok:true} with no envelope
// Every answered request echoes the request line it saw, the process id and the working directory, so tests can see what reached the child.
import { createInterface } from 'node:readline';
const flags = Object.fromEntries((process.env.FAKE_SERVICE ?? '').split(',').filter(Boolean).map((f) => { const [k, v] = f.split('='); return [k, v ?? true]; }));
const ready = { event: 'ready', protocol: 'kriya-kup-stdio/1', pid: process.pid, schema_version: 1, capability_set: '1.1' };
const write = (o) => process.stdout.write(`${typeof o === 'string' ? o : JSON.stringify(o)}\n`);
if (flags.ready === 'garbage') write('this is not json');
else if (flags.ready === 'skip') { /* never announce */ }
else if (flags.ready === 'late') setTimeout(() => write(ready), 400);
else write(ready);
let answered = 0;
createInterface({ input: process.stdin }).on('line', (line) => {
  const req = JSON.parse(line);
  if (flags['die-on'] === req.operation) process.exit(3);
  if (flags['hang-on'] === req.operation) return;
  if (flags['garbage-on'] === req.operation) return write('}{ not json');
  if (flags['big-on'] === req.operation) return write(`{"id":"${req.id}","ok":true,"envelope":{"pad":"${'x'.repeat(2048)}"}}`);
  if (flags['refuse-on'] === req.operation) return write({ id: req.id, ok: false, error: { code: 'INVALID_REQUEST', message: 'refused by the stand-in' } });
  if (flags['noenvelope-on'] === req.operation) return write({ id: req.id, ok: true });
  const id = flags['wrong-id'] ? 'nope' : req.id;
  write({ id, ok: true, envelope: { operation: req.operation, echoed: req, pid: process.pid, cwd: process.cwd(), env: { ...process.env }, error: null, data: {} } });
  answered += 1;
  if (flags['die-after'] && answered >= Number(flags['die-after'])) setTimeout(() => process.exit(3), 10);
});
process.stdin.on('end', () => process.exit(0));
