# {{company_title}}

This company runs on the Agents Office engine. Everything it does is defined by the files in this folder:

```
departments/*.yaml   one file per department: its agents, their tools, pipelines, prompts and approval rules
intake.yaml          which inbox files become which tasks
workspace/
  inbox/             drop files here (each subfolder is watched by a rule in intake.yaml)
  data/              reference data your tools read (price lists, rates, contacts...)
  tasks/ approvals/ logs/ outputs/   generated state (not committed to git)
```

## Try it

```bash
echo "Hello from our first customer" > companies/{{company}}/workspace/inbox/notes/hello.txt
python office.py --company {{company}} run
python office.py --company {{company}} city
```

The Inbox Clerk picks up the note and writes an intake report to `workspace/outputs/reports/intake/`.

## Grow it

1. Add a department: copy `departments/operations.yaml`, give it a new `id`, `name` and agents.
2. Give agents tools from the engine (`python office.py --company {{company}} check` lists problems), or write
   new ones in `officekit/tools/` with the `@tool` decorator.
3. Anything risky (money, emailing customers, publishing) should use `approval: required`, so it waits for you.
4. Make it the default in `office.yaml` (`default_company: {{company}}`) to drop the `--company` flag.
