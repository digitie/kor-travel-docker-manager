import type { Metadata } from 'next';
import DashboardClient from '../components/DashboardClient';

export const metadata: Metadata = {
  title: 'TripMate Infrastructure Manager',
  description: 'PostgreSQL & RustFS Container Management Control Center for TripMate Developers',
};

export default function Page() {
  return <DashboardClient />;
}
