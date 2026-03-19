```typescript
import { z } from 'zod';

export const userInputSchema = z.object({
  username: z.string().min(1, 'Username is required'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
});

export function validateUserInput(data: any) {
  try {
    return userInputSchema.parse(data);
  } catch (error) {
    throw error;
  }
}
```