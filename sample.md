# nanopeek demo

Welcome to **nanopeek** — a `nano`-style editor with a *live* Markdown
preview. Edit on the left, watch it render on the right.

## Text

You can write **bold**, *italic*, `inline code`, ~~strikethrough~~ and
[links](https://example.com). 

## Lists

1. Ordered lists work
2. So do nested items
   - like this one
   - and this one

- [x] task lists
- [ ] render too

## Code

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"

print(greet("nanopeek"))
```

> Block quotes are rendered with a nice margin,
> exactly like you would expect.

## Tables

| Key   | Action              |
|-------|---------------------|
| `^O`  | Write out (save as) |
| `^S`  | Save                |
| `^X`  | Exit                |
| `^K`  | Cut line            |
| `^U`  | Uncut               |
| `^P`  | Toggle preview      |

---

That's a horizontal rule. Start editing to see the preview update!
