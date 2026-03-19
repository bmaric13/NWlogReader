```typescript
import type { NextPage } from 'next';
import Head from 'next/head';
import styles from '../styles/Home.module.css';

const Home: NextPage = () => {
  return (
    <div className={styles.container}>
      <Head>
        <title>NW Log Reader</title>
        <meta name="description" content="NW Log Reader" />
      </Head>
      <main className={styles.main}>
        <h1 className={styles.title}>NW Log Reader</h1>
      </main>
    </div>
  );
};

export default Home;
```