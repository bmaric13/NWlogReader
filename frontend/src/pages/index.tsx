```typescript
import { useSession } from 'next-auth/react';
import { useRouter } from 'next/router';
import { useQuery } from 'react-query';
import axios from 'axios';
import { useState } from 'react';
import styles from '../styles/Home.module.css';

interface LogEntry {
  timestamp: string;
  level: string;
  message: string;
  source: string;
  details?: Record<string, unknown>;
}

export default function Home() {
  const { data: session, status } = useSession();
  const router = useRouter();
  const [filter, setFilter] = useState({
    level: '',
    source: '',
    search: ''
  });

  const { data: logs, error, isLoading } = useQuery<LogEntry[]>(
    ['logs', filter],
    async () => {
      if (!session?.accessToken) return [];

      const params = new URLSearchParams();
      if (filter.level) params.append('level', filter.level);
      if (filter.source) params.append('source', filter.source);
      if (filter.search) params.append('search', filter.search);

      const res = await axios.get('http://localhost:8000/api/v1/logs/', {
        headers: {
          Authorization: `Bearer ${session.accessToken}`
        },
        params
      });
      return res.data;
    },
    {
      enabled: status === 'authenticated'
    }
  );

  if (status === 'loading') {
    return <div className={styles.loading}>Loading...</div>;
  }

  if (status === 'unauthenticated') {
    router.push('/auth/signin');
    return null;
  }

  const handleFilterChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value } = e.target;
    setFilter(prev => ({ ...prev, [name]: value }));
  };

  return (
    <div className={styles.container}>
      <header className={styles.header}>
        <h1>NWlogReader</h1>
        <button onClick={() => router.push('/api/auth/signout')} className={styles.signOutButton}>
          Sign Out
        </button>
      </header>

      <div className={styles.filterSection}>
        <h2>Filters</h2>
        <div className={styles.filters}>
          <div className={styles.filterGroup}>
            <label htmlFor="level">Level:</label>
            <select id="level" name="level" value={filter.level} onChange={handleFilterChange}>
              <option value="">All</option>
              <option value="ERROR">Error</option>
              <option value="WARN">Warning</option>
              <option value="INFO">Info</option>
              <option value="DEBUG">Debug</option>
            </select>
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="source">Source:</label>
            <input
              type="text"
              id="source"
              name="source"
              value={filter.source}
              onChange={handleFilterChange}
              placeholder="Source filter"
            />
          </div>

          <div className={styles.filterGroup}>
            <label htmlFor="search">Search:</label>
            <input
              type="text"
              id="search"
              name="search"
              value={filter.search}
              onChange={handleFilterChange}
              placeholder="Search messages/sources"
            />
          </div>
        </div>
      </div>

      <div className={styles.logsContainer}>
        {isLoading ? (
          <div className={styles.loading}>Loading logs...</div>
        ) : error ? (
          <div className={styles.error}>Error loading logs: {(error as Error).message}</div>
        ) : (
          <table className={styles.logsTable}>
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Level</th>
                <th>Source</th>
                <th>Message</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {logs?.map((log, index) => (
                <tr key={index} className={styles[`logLevel${log.level}`]}>
                  <td>{new Date(log.timestamp).toLocaleString()}</td>
                  <td>{log.level}</td>
                  <td>{log.source}</td>
                  <td>{log.message}</td>
                  <td>
                    {log.details && (
                      <details>
                        <summary>View details</summary>
                        <pre>{JSON.stringify(log.details, null, 2)}</pre>
                      </details>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
```