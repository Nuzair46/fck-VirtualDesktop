using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

internal sealed class DeliveryFile
{
    public string path { get; set; }
    public long size { get; set; }
    public string sha256 { get; set; }
}
internal sealed class DeliveryManifest
{
    public int schema_version { get; set; }
    public int file_count { get; set; }
    public bool? portable { get; set; }
    public bool? application_binaries_included { get; set; }
    public bool? private_identity_included { get; set; }
    public List<DeliveryFile> files { get; set; }
}
internal sealed class BootstrapResult
{
    public bool success { get; set; }
    public string payload_sha256 { get; set; }
    public string cache_directory { get; set; }
    public bool cache_reused { get; set; }
    public int verified_files { get; set; }
    public bool gui_launched { get; set; }
    public string error { get; set; }
}

internal static class ToolBootstrap
{
    const string ArchivePrefix="VirtualDesktop-OfflineTool/";
    const string ManifestName="delivery-manifest.json";
    static string cacheRoot;
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer {MaxJsonLength=32*1024*1024};

    [STAThread]
    static int Main(string[] args)
    {
        bool verifyOnly=false;
        string report=null;
        try {
            for(int i=0;i<args.Length;i++) {
                if(args[i]=="--verify-only") verifyOnly=true;
                else if(args[i]=="--report" && i+1<args.Length) report=Path.GetFullPath(args[++i]);
                else throw new ArgumentException("Supported options: --verify-only --report PATH");
            }
            AppContext.SetSwitch("Switch.System.IO.UseLegacyPathHandling",false);
            AppContext.SetSwitch("Switch.System.IO.BlockLongPaths",false);
            string local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            Check(!String.IsNullOrWhiteSpace(local),"Windows LocalApplicationData is unavailable.");
            cacheRoot=Path.GetFullPath(Path.Combine(local,"VirtualDesktopOffline","tool-cache"));
            if(report!=null) Check(!Within(report,cacheRoot),"Write verification reports outside the tool cache.");
            if(verifyOnly) {
                BootstrapResult result;
                try {result=Prepare(delegate {});}
                catch(Exception ex) {result=Failure(ex);}
                if(report!=null) WriteReport(report,result);
                return result.success ? 0 : 1;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            int exitCode=1;
            using(var progress=new BootstrapProgress()) {
                progress.Shown+=delegate {
                    ThreadPool.QueueUserWorkItem(delegate {
                        BootstrapResult result;
                        try {
                            result=Prepare(progress.UpdateStatus);
                            Launch(result.cache_directory);
                            result.gui_launched=true;
                        } catch(Exception ex) {result=Failure(ex);}
                        if(report!=null) {
                            try {WriteReport(report,result);}
                            catch(Exception ex) {result=Failure(ex);}
                        }
                        progress.BeginInvoke(new Action(delegate {
                            exitCode=result.success ? 0 : 1;
                            progress.Completed=true;
                            progress.Close();
                            if(!result.success) MessageBox.Show(result.error,"Virtual Desktop Offline Tool",MessageBoxButtons.OK,MessageBoxIcon.Error);
                        }));
                    });
                };
                Application.Run(progress);
            }
            return exitCode;
        } catch(Exception ex) {
            if(verifyOnly && report!=null) {
                try {WriteReport(report,Failure(ex));} catch {}
            } else if(!verifyOnly) MessageBox.Show(ex.Message,"Virtual Desktop Offline Tool",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
    }
    static BootstrapResult Failure(Exception ex) {
        return new BootstrapResult {success=false,payload_sha256=BootstrapConfig.PayloadSha256,error=ex.Message};
    }
    static void Check(bool condition,string message) {if(!condition) throw new InvalidDataException(message);}
    static bool Within(string path,string directory) {
        return Path.GetFullPath(path).StartsWith(Path.GetFullPath(directory).TrimEnd(Path.DirectorySeparatorChar)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase);
    }
    static string Hash(Stream input) {
        using(var hash=SHA256.Create()) return BitConverter.ToString(hash.ComputeHash(input)).Replace("-", "").ToLowerInvariant();
    }
    static string FileHash(string path) {using(var input=File.OpenRead(path)) return Hash(input);}
    static byte[] Bytes(Stream stream) {using(var output=new MemoryStream()) {stream.CopyTo(output);return output.ToArray();}}
    static string ByteHash(byte[] bytes) {using(var input=new MemoryStream(bytes)) return Hash(input);}
    static void NoReparse(string path) {
        string current=Path.GetFullPath(path);
        while(!String.IsNullOrEmpty(current)) {
            if(File.Exists(current)||Directory.Exists(current))
                Check((File.GetAttributes(current)&FileAttributes.ReparsePoint)==0,"A symbolic link or junction is not allowed in the tool cache path: "+current);
            string parent=Path.GetDirectoryName(current);
            if(parent==current) break;
            current=parent;
        }
    }
    static string RelativePath(string path) {
        Check(!String.IsNullOrEmpty(path)&&!path.Contains('\\')&&!Path.IsPathRooted(path),"Invalid payload path.");
        foreach(string part in path.Split('/')) {
            Check(part!=""&&part!="."&&part!=".."&&part.TrimEnd(' ','.')==part,"Invalid payload path component: "+path);
            Check(part.IndexOfAny(Path.GetInvalidFileNameChars())<0,"Invalid Windows filename: "+path);
            string stem=part.Split('.')[0];
            Check(!Regex.IsMatch(stem,@"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$",RegexOptions.IgnoreCase),"Reserved Windows filename: "+path);
        }
        return path;
    }
    static string Destination(string root,string relative) {
        string result=Path.GetFullPath(Path.Combine(root,RelativePath(relative).Replace('/',Path.DirectorySeparatorChar)));
        Check(Within(result,root),"Payload path escaped its extraction directory.");
        return result;
    }
    static void ScopedCacheDirectory(string path) {
        Check(Within(path,cacheRoot)&&Path.GetDirectoryName(Path.GetFullPath(path))==cacheRoot,"Invalid cache version directory.");
        string name=Path.GetFileName(path);
        Check(Regex.IsMatch(name,@"^[a-f0-9]{64}(\.staging-[a-f0-9]{32})?$"),"Unexpected cache directory name.");
        NoReparse(path);
    }
    static DeliveryManifest ReadManifest(byte[] data) {
        var manifest=Json.Deserialize<DeliveryManifest>(Encoding.UTF8.GetString(data).TrimStart('\ufeff'));
        Check(manifest!=null&&manifest.schema_version==1&&manifest.portable==true,"The payload is not a supported portable delivery.");
        Check(manifest.application_binaries_included==false&&manifest.private_identity_included==false,"The payload contains application binaries or a private identity.");
        Check(manifest.files!=null&&manifest.file_count==manifest.files.Count&&manifest.file_count>0,"Invalid delivery file count.");
        var unique=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long total=0;
        foreach(var record in manifest.files) {
            RelativePath(record.path);
            Check(unique.Add(record.path)&&record.path!=ManifestName,"Duplicate delivery file path.");
            Check(record.size>=0&&Regex.IsMatch(record.sha256??"",@"^[a-f0-9]{64}$"),"Invalid delivery hash or size.");
            total=checked(total+record.size);
            string extension=Path.GetExtension(record.path).ToLowerInvariant();
            Check(extension!=".apk"&&extension!=".p12"&&extension!=".password"&&extension!=".der","An application or private signing file is not allowed in the tool payload.");
        }
        Check(total<=4L*1024*1024*1024,"Payload extraction size exceeds the tool limit.");
        foreach(string required in new [] {"toolchain_cli.py","tools/VdOfflineTool.exe","runtime/python/python.exe","runtime/dotnet/dotnet.exe"})
            Check(unique.Contains(required),"Portable payload is missing "+required);
        return manifest;
    }
    static Dictionary<string,ZipArchiveEntry> Entries(ZipArchive archive,DeliveryManifest manifest) {
        var expected=new HashSet<string>(manifest.files.Select(f=>f.path),StringComparer.OrdinalIgnoreCase);
        expected.Add(ManifestName);
        var directories=new HashSet<string>(StringComparer.OrdinalIgnoreCase) {""};
        foreach(string name in expected) {
            int pos=name.LastIndexOf('/');
            while(pos>=0) {directories.Add(name.Substring(0,pos));pos=name.LastIndexOf('/',pos-1);}
        }
        var entries=new Dictionary<string,ZipArchiveEntry>(StringComparer.OrdinalIgnoreCase);
        foreach(var entry in archive.Entries) {
            Check(entry.FullName.StartsWith(ArchivePrefix,StringComparison.Ordinal),"Unexpected ZIP root or member.");
            string relative=entry.FullName.Substring(ArchivePrefix.Length);
            int unixMode=(entry.ExternalAttributes>>16)&0xf000;
            Check(unixMode!=0xa000&&(entry.ExternalAttributes&(int)FileAttributes.ReparsePoint)==0,"Links are not allowed in the payload.");
            if(entry.FullName.EndsWith("/",StringComparison.Ordinal)) {
                Check(directories.Contains(relative.TrimEnd('/')),"Unexpected ZIP directory.");
                continue;
            }
            RelativePath(relative);
            Check(expected.Contains(relative)&&!entries.ContainsKey(relative),"Unexpected or duplicate ZIP member: "+relative);
            entries.Add(relative,entry);
        }
        Check(entries.Count==expected.Count,"The ZIP does not match its delivery manifest.");
        foreach(var record in manifest.files) Check(entries[record.path].Length==record.size,"ZIP size differs from manifest: "+record.path);
        return entries;
    }
    static void VerifyCache(string root,DeliveryManifest manifest,string manifestHash,Action<string> progress) {
        ScopedCacheDirectory(root);
        string manifestPath=Destination(root,ManifestName);
        NoReparse(manifestPath);
        Check(FileHash(manifestPath)==manifestHash,"Cached delivery manifest changed.");
        var expected=new HashSet<string>(manifest.files.Select(f=>f.path),StringComparer.OrdinalIgnoreCase) {ManifestName};
        int count=0;
        foreach(var record in manifest.files) {
            string file=Destination(root,record.path);
            NoReparse(file);
            Check(File.Exists(file)&&new FileInfo(file).Length==record.size&&FileHash(file)==record.sha256,"Cached file verification failed: "+record.path);
            count++;
            if(count%35==0) progress("Verifying tools: "+count+" / "+manifest.file_count);
        }
        var pending=new Stack<string>(); pending.Push(root);
        while(pending.Count>0) {
            string directory=pending.Pop(); NoReparse(directory);
            foreach(string child in Directory.GetDirectories(directory)) {NoReparse(child);pending.Push(child);}
            foreach(string file in Directory.GetFiles(directory)) {
                NoReparse(file);
                string relative=file.Substring(root.Length+1).Replace('\\','/');
                Check(expected.Contains(relative),"An unexpected file exists in the tool cache: "+relative);
            }
        }
    }
    static BootstrapResult Prepare(Action<string> progress) {
        Check(Regex.IsMatch(BootstrapConfig.PayloadSha256,@"^[a-f0-9]{64}$"),"Invalid embedded payload identity.");
        progress("Verifying embedded tools...");
        using(Stream payload=Assembly.GetExecutingAssembly().GetManifestResourceStream("VdOfflinePayload.zip")) {
            Check(payload!=null&&payload.CanSeek,"The embedded tool payload is missing.");
            Check(Hash(payload)==BootstrapConfig.PayloadSha256,"The embedded tool payload failed its SHA-256 check.");
            payload.Position=0;
            using(var zip=new ZipArchive(payload,ZipArchiveMode.Read,false)) {
                var rawManifest=zip.Entries.Where(e=>e.FullName==ArchivePrefix+ManifestName).ToArray();
                Check(rawManifest.Length==1&&rawManifest[0].Length<=32*1024*1024,"Missing or oversized delivery manifest.");
                byte[] bytes; using(var entry=rawManifest[0].Open()) bytes=Bytes(entry);
                var manifest=ReadManifest(bytes);
                var entries=Entries(zip,manifest);
                string manifestHash=ByteHash(bytes);
                NoReparse(cacheRoot); Directory.CreateDirectory(cacheRoot); NoReparse(cacheRoot);
                string final=Path.Combine(cacheRoot,BootstrapConfig.PayloadSha256);
                ScopedCacheDirectory(final);
                using(var mutex=new Mutex(false,"Local\\VirtualDesktopOffline-"+BootstrapConfig.PayloadSha256)) {
                    bool held=false;
                    try {
                        try {held=mutex.WaitOne(TimeSpan.FromMinutes(2));} catch(AbandonedMutexException) {held=true;}
                        Check(held,"Another copy is preparing this tool. Try again after it finishes.");
                        if(Directory.Exists(final)) {
                            try {VerifyCache(final,manifest,manifestHash,progress);}
                            catch(Exception ex) {throw new InvalidDataException("The cached tool was changed or is incomplete. This version was left untouched. Inspect or remove this exact version folder before retrying: "+final+Environment.NewLine+ex.Message,ex);}
                            return new BootstrapResult {success=true,payload_sha256=BootstrapConfig.PayloadSha256,cache_directory=final,cache_reused=true,verified_files=manifest.file_count};
                        }
                        Check(!File.Exists(final),"A file occupies the cache version directory.");
                        string stage=final+".staging-"+Guid.NewGuid().ToString("N");
                        ScopedCacheDirectory(stage); Directory.CreateDirectory(stage); NoReparse(stage);
                        int count=0;
                        foreach(var item in entries) {
                            string target=Destination(stage,item.Key);
                            string directory=Path.GetDirectoryName(target);
                            NoReparse(directory); Directory.CreateDirectory(directory); NoReparse(directory);
                            using(var source=item.Value.Open()) using(var output=new FileStream(target,FileMode.CreateNew,FileAccess.Write,FileShare.None)) source.CopyTo(output);
                            count++;
                            if(count%35==0) progress("Preparing tools: "+count+" / "+entries.Count);
                        }
                        VerifyCache(stage,manifest,manifestHash,progress);
                        // Publish only a completely verified directory. No cache
                        // folder is recursively deleted or overwritten, even on failure.
                        ScopedCacheDirectory(stage); ScopedCacheDirectory(final);
                        Check(!Directory.Exists(final)&&!File.Exists(final),"The cache destination appeared during preparation.");
                        Directory.Move(stage,final);
                        return new BootstrapResult {success=true,payload_sha256=BootstrapConfig.PayloadSha256,cache_directory=final,cache_reused=false,verified_files=manifest.file_count};
                    } finally {if(held) mutex.ReleaseMutex();}
                }
            }
        }
    }
    static string Quote(string value) {
        var result=new StringBuilder("\""); int slashes=0;
        foreach(char c in value) {
            if(c=='\\') {slashes++;continue;}
            result.Append('\\',c=='"' ? slashes*2+1 : slashes); result.Append(c); slashes=0;
        }
        result.Append('\\',slashes*2);result.Append('"');return result.ToString();
    }
    static void Launch(string root) {
        var start=new ProcessStartInfo();
        start.FileName=Path.Combine(root,"tools","VdOfflineTool.exe");
        start.Arguments="--root "+Quote(root)+" --python "+Quote(Path.Combine(root,"runtime","python","python.exe"));
        start.WorkingDirectory=root;start.UseShellExecute=false;start.CreateNoWindow=true;
        start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"]="1";
        start.EnvironmentVariables["PYTHONUTF8"]="1";
        string dotnet=Path.Combine(root,"runtime","dotnet");
        start.EnvironmentVariables["PATH"]=dotnet+";"+start.EnvironmentVariables["PATH"];
        start.EnvironmentVariables["DOTNET_ROOT"]=dotnet;
        start.EnvironmentVariables["DOTNET_ROOT_X64"]=dotnet;
        Process.Start(start);
    }
    static void WriteReport(string path,BootstrapResult result) {
        string full=Path.GetFullPath(path);
        if(cacheRoot!=null) Check(!Within(full,cacheRoot),"Report paths must be outside the cache.");
        Directory.CreateDirectory(Path.GetDirectoryName(full));
        File.WriteAllText(full,Json.Serialize(result)+Environment.NewLine,new UTF8Encoding(false));
    }
}

internal sealed class BootstrapProgress : Form
{
    readonly Label label=new Label();
    public bool Completed;
    public BootstrapProgress() {
        Text="Virtual Desktop Offline Tool";ClientSize=new Size(430,105);
        FormBorderStyle=FormBorderStyle.FixedDialog;MaximizeBox=false;MinimizeBox=false;
        StartPosition=FormStartPosition.CenterScreen;Font=new Font("Segoe UI",10F);
        label.Text="Preparing the local tool...";label.Dock=DockStyle.Fill;label.TextAlign=ContentAlignment.MiddleCenter;
        Controls.Add(label);
        FormClosing+=delegate(object sender,FormClosingEventArgs e) {if(!Completed)e.Cancel=true;};
    }
    public void UpdateStatus(string value) {BeginInvoke(new Action(delegate {label.Text=value;}));}
}
