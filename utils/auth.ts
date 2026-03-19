```typescript
import jwt from 'jsonwebtoken';

const secretKey = process.env.SECRET_KEY;

export function verifyToken(token: string) {
  return new Promise((resolve, reject) => {
    jwt.verify(token, secretKey, (err, user) => {
      if (err) {
        reject(err);
      } else {
        resolve(user);
      }
    });
  });
}
```