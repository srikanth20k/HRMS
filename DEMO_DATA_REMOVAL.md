# Removing Demo/Seed Data

## ✓ Completed (Backend)

### 1. Database Seed Data
- ✓ Cleared `SEED_JOBS` list in [api/seed.py](api/seed.py)
- ✓ Cleared `SEED_INTERVIEWS` list in [api/seed.py](api/seed.py)
- ✓ Removed default seed admin from [api/migrations/0003_app_users.py](api/migrations/0003_app_users.py)

### 2. Clean Existing Demo Data
To remove any demo data already in your database, run:

```bash
cd hrms_django
python manage.py cleanup_demo_data
```

This will delete:
- All 6 demo jobs (Senior Software Engineer, Product Manager, Data Scientist, etc.)
- All 5 demo interviews (Ravi Kumar, Ananya Singh, Vikram Nair, Priya Mehta, Arjun Das)
- The default admin user (`admin@eversoftit.com`)

---

## ⏳ TODO (React Frontend)

The React app is compiled into `dist/`, so the following must be removed from the **React source code** (when available):

### 1. Remove "Demo login" hint
**Location:** Auth/Login page
**Current text:** "Demo login — admin@eversoftit.com / admin123"
**Action:** Delete this entire hint block

**Code pattern to find:**
```javascript
a==="login"&&n.jsxs("div",{className:"auth-hint",children:["Demo login — ",n.jsx("code",{children:"admin@eversoftit.com"})," / ",n.jsx("code",{children:"admin123"})]})
```

### 2. Remove hardcoded demo user from localStorage
**Location:** Context/Auth provider initialization
**Current data:**
```javascript
Ro=[{name:"EverSoft Admin",email:"admin@eversoftit.com",password:"admin123",role:Ke}]
```
**Action:** Change to empty array `[]` so localStorage starts empty. Users must create logins via the app UI.

### 3. Remove "HRMS Demo Corp" company name
**Location:** Settings → General tab
**Current value:** "HRMS Demo Corp"
**Action:** Change to empty string or actual company name, or remove the hardcoded default

**Code pattern:**
```javascript
["Company Name","HRMS Demo Corp"]
```

### 4. After changes
Once the React source is updated, rebuild the app:
```bash
cd <react-project-root>
npm run build
# Copy dist/ to hrms-django-deploy/dist/
```

---

## Result

After these changes:
- ✅ No demo jobs/interviews in database
- ✅ No demo admin user
- ✅ No "Demo login" hint on the UI
- ✅ Users must create all logins explicitly
- ✅ UI shows only real, user-entered database data
