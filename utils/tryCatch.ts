```typescript
export function tryCatch(func: () => void) {
  try {
    func();
  } catch (error) {
    console.error(error);
  }
}
```